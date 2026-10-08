"""Game catalogue: search, lookup, metadata and shop resolution."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.core.errors import NotFoundError, UpstreamUnavailable, ValidationFailed
from app.core.logging import get_logger
from app.core.timeutil import utcnow
from app.models import Game, Shop
from app.providers.pricing.base import ProviderError, ProviderRateLimited
from app.repositories import games as games_repo

logger = get_logger(__name__)


@asynccontextmanager
async def provider_errors() -> AsyncIterator[None]:
    """Translate provider failures into the API's upstream error (no provider detail leaks)."""
    try:
        yield
    except ProviderRateLimited as exc:
        headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else None
        raise UpstreamUnavailable(
            "The price data provider is rate limiting requests. Try again shortly.",
            headers=headers,
        ) from exc
    except ProviderError as exc:
        logger.warning("provider call failed", extra={"code": exc.code})
        raise UpstreamUnavailable() from exc


class CatalogService:
    def __init__(self, session: AsyncSession, container: Container) -> None:
        self.session = session
        self.container = container
        self.provider = container.price_provider
        self.settings = container.settings

    async def steam_shop(self) -> Shop:
        """The Steam shop row, discovered from the provider's shop list by name rather
        than a hard-coded provider id."""
        slug = games_repo.slugify(self.settings.steam_shop_name)
        shop = await games_repo.get_shop_by_slug(self.session, slug)
        if shop is None:
            async with provider_errors():
                shops = await self.provider.list_shops(self.settings.default_country)
            await games_repo.upsert_shops(self.session, shops)
            await self.session.commit()
            shop = await games_repo.get_shop_by_slug(self.session, slug)
        if shop is None:
            raise UpstreamUnavailable("The Steam shop could not be resolved from the provider.")
        return shop

    async def resolve_shop(self, slug: str) -> Shop:
        steam = await self.steam_shop()
        if slug.strip().lower() != steam.slug:
            raise ValidationFailed(
                "Only the Steam shop is supported.", details={"supported_shops": [steam.slug]}
            )
        return steam

    async def sync_shops(self) -> int:
        shops = await self.provider.list_shops(self.settings.default_country)
        await games_repo.upsert_shops(self.session, shops)
        await self.session.commit()
        return len(shops)

    async def search(self, query: str, limit: int = 20) -> list[Game]:
        try:
            found = await self.provider.search_games(query, limit)
        except ProviderError as exc:
            # Degrade to what is already known locally instead of failing the search.
            logger.warning("search fell back to local catalogue", extra={"code": exc.code})
            local = await games_repo.search_local(self.session, query, limit)
            if local:
                return local
            async with provider_errors():
                raise
        games = await games_repo.upsert_provider_games(self.session, found)
        await self.session.commit()
        return games

    async def lookup(self, *, steam_app_id: int | None = None, title: str | None = None) -> Game:
        if steam_app_id is not None:
            existing = await games_repo.get_by_steam_app_id(self.session, steam_app_id)
            if existing is not None:
                return existing
        async with provider_errors():
            found = await self.provider.lookup_game(title=title, steam_app_id=steam_app_id)
        if found is None:
            raise NotFoundError("No game matched the lookup.")
        (game,) = await games_repo.upsert_provider_games(self.session, [found])
        if steam_app_id is not None and game.steam_app_id is None:
            game.steam_app_id = steam_app_id
        await self.session.commit()
        return await self.ensure_info(game)

    async def get(self, game_id: uuid.UUID) -> Game:
        game = await games_repo.get(self.session, game_id)
        if game is None:
            raise NotFoundError("Game not found.")
        return game

    async def get_with_info(self, game_id: uuid.UUID) -> Game:
        return await self.ensure_info(await self.get(game_id))

    async def ensure_info(self, game: Game, *, force: bool = False) -> Game:
        """Fetch full metadata once, then only when it is older than the refresh window.
        Metadata is best-effort: a provider outage returns what is already stored."""
        max_age = timedelta(days=self.settings.metadata_refresh_days)
        fresh = game.info_fetched_at is not None and utcnow() - game.info_fetched_at < max_age
        if game.itad_id is None or (fresh and not force):
            return game
        try:
            info = await self.provider.get_game_info(game.itad_id)
        except ProviderError as exc:
            logger.warning("metadata refresh failed", extra={"code": exc.code})
            return game
        if info is not None:
            await games_repo.apply_info(self.session, game, info)
            await self.session.commit()
        return game
