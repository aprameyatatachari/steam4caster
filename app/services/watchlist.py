"""Watchlist CRUD and the portfolio-style summary. Every query is scoped to the owner."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.core.logging import get_logger
from app.core.money import normalize_country, normalize_currency
from app.forecasting.policy import probability_within
from app.models import Forecast, Game, Recommendation, RegionalGamePrice, User, WatchlistEntry
from app.models.enums import Channel
from app.providers.pricing.base import ProviderError
from app.repositories import forecasts as forecasts_repo
from app.repositories import prices as prices_repo
from app.services.catalog import CatalogService
from app.services.prices import PriceService

logger = get_logger(__name__)

SUMMARY_HORIZONS = (7, 30, 90)
LIKELY_SALE_PROBABILITY = 0.6
_UNSET: Any = object()


@dataclass
class EntryView:
    entry: WatchlistEntry
    game: Game
    price: RegionalGamePrice | None = None
    forecast: Forecast | None = None
    recommendation: Recommendation | None = None


@dataclass
class CurrencySummary:
    currency: str
    games_priced: int = 0
    current_total_minor: int = 0
    historical_low_total_minor: int = 0
    games_with_historical_low: int = 0
    expected_total_minor: dict[int, int] = field(default_factory=dict)
    games_with_forecast: int = 0


def _channels(values: list[str] | None) -> list[str]:
    if values is None:
        return [Channel.WEB_PUSH.value, Channel.EMAIL.value]
    try:
        return sorted({Channel(v).value for v in values})
    except ValueError as exc:
        raise ValidationFailed("Unknown notification channel.") from exc


class WatchlistService:
    def __init__(self, session: AsyncSession, container: Container) -> None:
        self.session = session
        self.container = container

    async def _owned(self, user: User, entry_id: uuid.UUID) -> WatchlistEntry:
        entry = await self.session.get(WatchlistEntry, entry_id)
        # Same response whether the entry is missing or owned by someone else.
        if entry is None or entry.user_id != user.id:
            raise NotFoundError("Watchlist entry not found.")
        return entry

    async def _view(self, entries: list[WatchlistEntry], shop_id: int) -> list[EntryView]:
        if not entries:
            return []
        games = {
            g.id: g
            for g in await self.session.scalars(
                select(Game).where(Game.id.in_({e.game_id for e in entries}))
            )
        }
        keys = [(e.game_id, e.country) for e in entries]
        prices = await prices_repo.regional_prices_for(self.session, keys, shop_id)
        forecasts = await forecasts_repo.latest_forecasts_for(self.session, keys, shop_id)
        views: list[EntryView] = []
        for entry in entries:
            key = (entry.game_id, entry.country)
            forecast = forecasts.get(key)
            recommendation = None
            if forecast is not None:
                recommendation = await forecasts_repo.find_recommendation(
                    self.session,
                    forecast.id,
                    entry.max_wait_days or self.container.policy_config.default_max_wait_days,
                    entry.id,
                )
            views.append(
                EntryView(entry, games[entry.game_id], prices.get(key), forecast, recommendation)
            )
        return views

    async def list_entries(self, user: User) -> list[EntryView]:
        shop = await CatalogService(self.session, self.container).steam_shop()
        entries = list(
            await self.session.scalars(
                select(WatchlistEntry)
                .where(WatchlistEntry.user_id == user.id)
                .order_by(WatchlistEntry.created_at.desc())
            )
        )
        return await self._view(entries, shop.id)

    async def get(self, user: User, entry_id: uuid.UUID) -> EntryView:
        shop = await CatalogService(self.session, self.container).steam_shop()
        return (await self._view([await self._owned(user, entry_id)], shop.id))[0]

    async def create(
        self,
        user: User,
        *,
        game_id: uuid.UUID,
        country: str | None,
        currency: str | None,
        target_price_minor: int | None,
        min_discount_pct: int | None,
        max_wait_days: int | None,
        historical_low_only: bool,
        notify_on_buy: bool,
        channels: list[str] | None,
    ) -> EntryView:
        catalog = CatalogService(self.session, self.container)
        game = await catalog.get(game_id)
        shop = await catalog.steam_shop()
        try:
            region = normalize_country(country or user.default_country)
            requested_currency = normalize_currency(currency) if currency else None
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        # Learn the region's real currency so a target price is never compared across
        # currencies. Best effort: the entry is still created during a provider outage.
        regional = None
        try:
            regional = await PriceService(self.session, self.container).get_current(
                game, shop, region
            )
        except ProviderError as exc:
            logger.warning(
                "price unavailable while adding watchlist entry", extra={"code": exc.code}
            )
        if regional is not None and requested_currency not in (None, regional.currency):
            raise ValidationFailed(
                f"Prices for {region} are quoted in {regional.currency}; the target price "
                f"cannot be given in {requested_currency}.",
                details={"expected_currency": regional.currency},
            )
        entry = WatchlistEntry(
            user_id=user.id,
            game_id=game.id,
            country=region,
            currency=(regional.currency if regional else None)
            or requested_currency
            or user.default_currency,
            target_price_minor=target_price_minor,
            min_discount_pct=min_discount_pct,
            max_wait_days=max_wait_days,
            historical_low_only=historical_low_only,
            notify_on_buy=notify_on_buy,
            channels=_channels(channels),
        )
        self.session.add(entry)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("This game is already on your watchlist.") from exc
        self.container.tasks.enqueue("prices.process_series", str(game.id), region)
        return (await self._view([entry], shop.id))[0]

    async def update(self, user: User, entry_id: uuid.UUID, changes: dict[str, Any]) -> EntryView:
        entry = await self._owned(user, entry_id)
        if "channels" in changes:
            changes["channels"] = _channels(changes["channels"])
        if changes.get("currency") is not None:
            try:
                wanted = normalize_currency(changes.pop("currency"))
            except ValueError as exc:
                raise ValidationFailed(str(exc)) from exc
            if wanted != entry.currency:
                raise ValidationFailed(
                    f"This entry's prices are quoted in {entry.currency}.",
                    details={"expected_currency": entry.currency},
                )
        changes.pop("currency", None)
        for name, value in changes.items():
            setattr(entry, name, value)
        await self.session.commit()
        return await self.get(user, entry_id)

    async def delete(self, user: User, entry_id: uuid.UUID) -> None:
        await self.session.delete(await self._owned(user, entry_id))
        await self.session.commit()

    async def summary(self, user: User) -> dict[str, Any]:
        """Totals are grouped per currency and never summed across currencies. Values
        derived from forecasts are estimates and are labelled as such by the API."""
        views = [v for v in await self.list_entries(user) if v.entry.is_active]
        by_currency: dict[str, CurrencySummary] = {}
        counts = {"BUY": 0, "WAIT": 0, "NEUTRAL": 0, "UNAVAILABLE": 0}
        likely: list[dict[str, Any]] = []
        for view in views:
            action = view.recommendation.action if view.recommendation else "UNAVAILABLE"
            counts[action] += 1
            price, forecast = view.price, view.forecast
            if price is None:
                continue
            bucket = by_currency.setdefault(price.currency, CurrencySummary(price.currency))
            bucket.games_priced += 1
            bucket.current_total_minor += price.price_minor
            if price.historical_low_minor is not None:
                bucket.games_with_historical_low += 1
                bucket.historical_low_total_minor += price.historical_low_minor
            else:
                bucket.historical_low_total_minor += price.price_minor
            usable = (
                forecast is not None
                and forecast.currency == price.currency
                and forecast.regular_price_minor is not None
            )
            if usable:
                assert forecast is not None and forecast.regular_price_minor is not None
                bucket.games_with_forecast += 1
                probs = {
                    7: forecast.new_sale_prob_7d,
                    30: forecast.new_sale_prob_30d,
                    90: forecast.new_sale_prob_90d,
                }
                sale_price = round(
                    forecast.regular_price_minor * (1 - forecast.expected_discount_pct / 100)
                )
                # While a sale is running the buyer can simply take today's price.
                no_sale = price.price_minor
                for horizon in SUMMARY_HORIZONS:
                    p = probability_within(probs, horizon)
                    expected = round(p * min(sale_price, no_sale) + (1 - p) * no_sale)
                    bucket.expected_total_minor[horizon] = (
                        bucket.expected_total_minor.get(horizon, 0) + expected
                    )
                if not forecast.currently_on_sale and probs[30] >= LIKELY_SALE_PROBABILITY:
                    likely.append(
                        {
                            "entry_id": view.entry.id,
                            "game_id": view.game.id,
                            "title": view.game.title,
                            "sale_probability_30d": probs[30],
                            "window_start": forecast.window_start,
                            "window_end": forecast.window_end,
                        }
                    )
            else:
                for horizon in SUMMARY_HORIZONS:
                    bucket.expected_total_minor[horizon] = (
                        bucket.expected_total_minor.get(horizon, 0) + price.price_minor
                    )
        likely.sort(key=lambda item: item["sale_probability_30d"], reverse=True)
        return {
            "entries": len(views),
            "totals": list(by_currency.values()),
            "recommendation_counts": counts,
            "likely_on_sale_within_30d": likely,
        }
