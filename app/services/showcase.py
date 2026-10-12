"""Read-only views for the public landing page: current deals and a per-game demo."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.models import Game
from app.providers.pricing.base import ProviderDeal, ProviderListedDeal
from app.repositories import games as games_repo
from app.services.catalog import CatalogService, provider_errors
from app.services.forecasts import ForecastService
from app.services.prices import PriceService

DEALS_POOL = 100
SNAPSHOT_HISTORY_LIMIT = 5000


class ShowcaseService:
    def __init__(self, session: AsyncSession, container: Container) -> None:
        self.session = session
        self.container = container
        self.settings = container.settings

    async def best_deals(self, country: str, limit: int) -> list[tuple[Game, ProviderDeal]]:
        """Steam's notable current sales: the most popular games first, keeping full
        games discounted by at least the configured cut."""
        shop = await CatalogService(self.session, self.container).steam_shop()
        async with provider_errors():
            listed = await self.container.price_provider.list_deals(
                country, [shop.provider_shop_id], DEALS_POOL
            )
        floor = self.settings.best_deals_min_discount_pct

        def eligible(item: ProviderListedDeal) -> bool:
            deal = item.deal
            return (
                item.game.type in (None, "game")
                and deal.shop_id == shop.provider_shop_id
                and deal.cut >= floor
                and deal.price.currency == deal.regular.currency
                and deal.regular.amount_minor > 0
            )

        chosen = [item for item in listed if eligible(item)][:limit]
        games = await games_repo.upsert_provider_games(self.session, [c.game for c in chosen])
        await self.session.commit()
        return [(game, item.deal) for game, item in zip(games, chosen, strict=True)]

    async def snapshot(
        self, game_id: uuid.UUID, country: str, max_wait_days: int
    ) -> dict[str, Any]:
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
