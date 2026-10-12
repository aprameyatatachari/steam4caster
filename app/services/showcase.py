"""Read-only views for the public landing page: current deals and a per-game demo."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.timeutil import utcnow
from app.models import Game
from app.providers.pricing.base import ProviderDeal, ProviderError, ProviderListedDeal
from app.repositories import games as games_repo
from app.services.catalog import CatalogService, provider_errors
from app.services.forecasts import ForecastService
from app.services.prices import PriceService

DEALS_POOL = 100
# The landing page shows this many deals, and only these games can be demonstrated.
SHOWCASE_SIZE = 10
SNAPSHOT_HISTORY_LIMIT = 5000
# How long the last good deals list may stand in when the provider is failing.
LAST_GOOD_TTL_SECONDS = 24 * 3600

logger = get_logger(__name__)
_LISTED = TypeAdapter(list[ProviderListedDeal])


class ShowcaseService:
    def __init__(self, session: AsyncSession, container: Container) -> None:
        self.session = session
        self.container = container
        self.settings = container.settings

    async def best_deals(self, country: str, limit: int) -> list[tuple[Game, ProviderDeal]]:
        """Steam's notable current sales: the most popular games first, keeping full
        games discounted by at least the configured cut."""
        shop = await CatalogService(self.session, self.container).steam_shop()
        floor = self.settings.best_deals_min_discount_pct
        last_good = f"showcase:deals:{country}"

        def eligible(item: ProviderListedDeal) -> bool:
            deal = item.deal
            return (
                item.game.type in (None, "game")
                and deal.shop_id == shop.provider_shop_id
                and deal.cut >= floor
                and deal.price.currency == deal.regular.currency
                and deal.regular.amount_minor > 0
            )

        try:
            listed = await self.container.price_provider.list_deals(
                country, [shop.provider_shop_id], DEALS_POOL
            )
        except ProviderError as exc:
            # The landing page should not go blank because the provider is throttling:
            # serve the last list we saw, minus any sale that has since ended.
            stored = await self.container.kv.get(last_good)
            if stored is None:
                async with provider_errors():
                    raise
            logger.warning("serving the last good deals list", extra={"code": exc.code})
            now = utcnow()
            chosen = [
                item
                for item in _LISTED.validate_json(stored)
                if item.deal.expiry is None or item.deal.expiry > now
            ][:limit]
        else:
            chosen = [item for item in listed if eligible(item)][:SHOWCASE_SIZE]
            await self.container.kv.set(
                last_good, _LISTED.dump_json(chosen).decode(), LAST_GOOD_TTL_SECONDS
            )
            chosen = chosen[:limit]
        games = await games_repo.upsert_provider_games(self.session, [c.game for c in chosen])
        await self.session.commit()
        return [(game, item.deal) for game, item in zip(games, chosen, strict=True)]

    async def snapshot(
        self, game_id: uuid.UUID, country: str, max_wait_days: int
    ) -> dict[str, Any]:
        # Without an account only this week's listed deals can be looked at, so the
        # landing page cannot stand in for the app.
        listed = {game.id for game, _ in await self.best_deals(country, SHOWCASE_SIZE)}
        if game_id not in listed:
            raise NotFoundError(
                "This game is not one of this week's deals. Create an account to look up any game."
            )
        catalog = CatalogService(self.session, self.container)
        game = await catalog.get_with_info(game_id)
        shop = await catalog.steam_shop()
        prices = PriceService(self.session, self.container)
        forecasts = ForecastService(self.session, self.container)
        async with provider_errors():
            forecast = await forecasts.get_or_generate(game, shop, country)
            price = await prices.get_current(game, shop, country)
        recommendation = await forecasts.recommend(forecast, max_wait_days=max_wait_days)
        history = await prices.list_observations(
            game, shop, country, start=None, end=None, after=None, limit=SNAPSHOT_HISTORY_LIMIT
        )
        return {
            "game": game,
            "shop": shop,
            "price": price,
            "history": history,
            "sale_events": await prices.list_sale_events(game, shop, country),
            "forecast": forecast,
            "recommendation": recommendation,
        }
