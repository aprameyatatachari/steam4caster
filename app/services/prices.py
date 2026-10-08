"""Regional prices, history ingestion and sale-event derivation.

Every regional price is an independent observation in the provider's currency for that
country. Nothing here ever converts between currencies.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.core.kv import distributed_lock
from app.core.logging import get_logger
from app.core.timeutil import utcnow
from app.db.session import upsert
from app.forecasting.sale_events import derive_sale_events
from app.models import Game, PriceObservation, RegionalGamePrice, SaleEvent, Shop
from app.providers.pricing.base import ProviderDeal, ProviderError
from app.repositories import prices as prices_repo

logger = get_logger(__name__)


@dataclass(frozen=True)
class IngestResult:
    skipped: bool
    inserted: int = 0
    sale_events: int = 0


class PriceService:
    def __init__(self, session: AsyncSession, container: Container) -> None:
        self.session = session
        self.container = container
        self.provider = container.price_provider
        self.settings = container.settings

    # --- current prices ------------------------------------------------------

    async def _store_deal(self, game: Game, shop: Shop, country: str, deal: ProviderDeal) -> bool:
        """Persist the current deal. Returns True when it is a new observation."""
        currency = deal.price.currency
        if deal.regular.currency != currency:
            logger.warning("provider deal with mixed currencies ignored")
            return False
        inserted = await prices_repo.insert_observations(
            self.session,
            [
                {
                    "game_id": game.id,
                    "shop_id": shop.id,
                    "country": country,
                    "currency": currency,
                    "price_minor": deal.price.amount_minor,
                    "regular_minor": deal.regular.amount_minor,
                    "discount_pct": deal.cut,
                    "observed_at": deal.timestamp,
                    "ingested_at": utcnow(),
                    "source": "current",
                }
            ],
        )
        low = await prices_repo.observed_low(self.session, game.id, shop.id, country, currency)
        low_minor, low_at = low if low else (deal.price.amount_minor, deal.timestamp)
        # The provider's own store low covers history we may not have ingested yet.
        if (
            deal.store_low
            and deal.store_low.currency == currency
            and deal.store_low.amount_minor < low_minor
        ):
            low_minor, low_at = deal.store_low.amount_minor, None  # type: ignore[assignment]
        await upsert(
            self.session,
            RegionalGamePrice.__table__,
            {
                "id": uuid.uuid4(),
                "game_id": game.id,
                "shop_id": shop.id,
                "country": country,
                "currency": currency,
                "price_minor": deal.price.amount_minor,
                "regular_minor": deal.regular.amount_minor,
                "discount_pct": deal.cut,
                "observed_at": deal.timestamp,
                "fetched_at": utcnow(),
                "url": deal.url,  # stored exactly as supplied (affiliate tags intact)
                "historical_low_minor": low_minor,
                "historical_low_at": low_at,
            },
            ["game_id", "shop_id", "country"],
            [
                "currency", "price_minor", "regular_minor", "discount_pct", "observed_at",
                "fetched_at", "url", "historical_low_minor", "historical_low_at",
            ],
        )  # fmt: skip
        return inserted > 0

    async def refresh_current(
        self, games: Sequence[Game], shop: Shop, country: str
    ) -> list[uuid.UUID]:
        """Batch-refresh current prices for one country. Returns ids of games whose
        price changed (a new observation was recorded). Idempotent under replays."""
        by_itad = {g.itad_id: g for g in games if g.itad_id}
        if not by_itad:
            return []
        results = await self.provider.get_current_prices(
            list(by_itad), country, [shop.provider_shop_id]
        )
        changed: list[uuid.UUID] = []
        for item in results:
            game = by_itad.get(item.provider_id)
            deal = next((d for d in item.deals if d.shop_id == shop.provider_shop_id), None)
            if game is None or deal is None:
                continue
            if await self._store_deal(game, shop, country, deal):
                changed.append(game.id)
        await self.session.commit()
        for game_id in changed:
            await self.rebuild_sale_events(game_id, shop, country)
        await self.session.commit()
        return changed

    async def get_current(
        self, game: Game, shop: Shop, country: str, *, allow_refresh: bool = True
    ) -> RegionalGamePrice | None:
        """Latest regional price, refreshed from the provider when stale. A provider
        outage returns the stored (possibly stale) row rather than an error."""
        row = await prices_repo.get_regional_price(self.session, game.id, shop.id, country)
        max_age = timedelta(seconds=self.settings.price_stale_after_seconds)
        if row is not None and utcnow() - row.fetched_at < max_age:
            return row
        if not allow_refresh:
            return row
        try:
            await self.refresh_current([game], shop, country)
        except ProviderError as exc:
            if row is None:
                raise
            logger.warning("serving stale price", extra={"code": exc.code})
            return row
        return await prices_repo.get_regional_price(self.session, game.id, shop.id, country)

    # --- history -------------------------------------------------------------

    async def ingest_history(
        self, game: Game, shop: Shop, country: str, *, full: bool = False
    ) -> IngestResult:
        """Backfill or incrementally extend the price history of one series.

        Restartable and replay-safe: observations are inserted with ON CONFLICT DO
        NOTHING and the watermark only advances after a successful, committed fetch.
        """
        if game.itad_id is None:
            return IngestResult(skipped=True)
        lock_name = f"ingest:{game.id}:{shop.id}:{country}"
        async with distributed_lock(self.container.kv, lock_name, ttl=300) as acquired:
            if not acquired:
                return IngestResult(skipped=True)
            watermark = await prices_repo.get_or_create_watermark(
                self.session, game.id, shop.id, country
            )
            backfill_start = datetime.combine(
                self.settings.history_backfill_start, time.min, tzinfo=UTC
            )
            if game.release_date is not None:
                release = datetime.combine(game.release_date, time.min, tzinfo=UTC)
                backfill_start = max(backfill_start, release - timedelta(days=30))
            if full or watermark.backfill_completed_at is None or watermark.covered_until is None:
                since = backfill_start
            else:
                # Re-read a small overlap so late provider corrections are picked up.
                since = watermark.covered_until - timedelta(
                    hours=self.settings.history_overlap_hours
                )
            requested_at = utcnow()
            try:
                points = await self.provider.get_price_history(
                    game.itad_id, country, [shop.provider_shop_id], since
                )
            except ProviderError as exc:
                watermark.last_error_code = exc.code
                await self.session.commit()
                raise
            rows = [
                {
                    "game_id": game.id,
                    "shop_id": shop.id,
                    "country": country,
                    "currency": p.price.currency,
                    "price_minor": p.price.amount_minor,
                    "regular_minor": p.regular.amount_minor,
                    "discount_pct": p.cut,
                    "observed_at": p.timestamp,
                    "ingested_at": requested_at,
                    "source": "history",
                }
                for p in points
                if p.shop_id == shop.provider_shop_id and p.price.currency == p.regular.currency
            ]
            inserted = await prices_repo.insert_observations(self.session, rows)
            watermark.history_since = min(filter(None, [watermark.history_since, since]))
            watermark.covered_until = requested_at
            watermark.backfill_completed_at = watermark.backfill_completed_at or requested_at
            watermark.last_success_at = requested_at
            watermark.last_error_code = None
            events = await self.rebuild_sale_events(game.id, shop, country)
            await self.session.commit()
            return IngestResult(skipped=False, inserted=inserted, sale_events=events)

    async def ensure_history(self, game: Game, shop: Shop, country: str) -> None:
        """Ingest history on demand when it is missing or stale. Best effort."""
        watermark = await prices_repo.get_watermark(self.session, game.id, shop.id, country)
        max_age = timedelta(seconds=self.settings.price_stale_after_seconds)
        if (
            watermark is not None
            and watermark.last_success_at is not None
            and utcnow() - watermark.last_success_at < max_age
        ):
            return
        try:
            await self.ingest_history(game, shop, country)
        except ProviderError as exc:
            logger.warning("on-demand history ingest failed", extra={"code": exc.code})

    async def rebuild_sale_events(self, game_id: uuid.UUID, shop: Shop, country: str) -> int:
        points, currency = await prices_repo.series_points(self.session, game_id, shop.id, country)
        if currency is None:
            return 0
        events = derive_sale_events(points)
        await prices_repo.replace_sale_events(
            self.session, game_id, shop.id, country, currency, events
        )
        return len(events)

    async def list_observations(
        self,
        game: Game,
        shop: Shop,
        country: str,
        *,
        start: datetime | None,
        end: datetime | None,
        after: tuple[datetime, int] | None,
        limit: int,
    ) -> list[PriceObservation]:
        return await prices_repo.list_observations(
            self.session, game.id, shop.id, country, start=start, end=end, after=after, limit=limit
        )

    async def list_sale_events(self, game: Game, shop: Shop, country: str) -> list[SaleEvent]:
        return await prices_repo.list_sale_events(self.session, game.id, shop.id, country)
