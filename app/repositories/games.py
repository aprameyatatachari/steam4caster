from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import utcnow
from app.db.session import insert_ignore
from app.models import Game, Shop
from app.providers.pricing.base import ProviderGame, ProviderGameInfo, ProviderShop


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


async def get(session: AsyncSession, game_id: uuid.UUID) -> Game | None:
    return await session.get(Game, game_id)


async def get_by_itad_ids(session: AsyncSession, itad_ids: Sequence[str]) -> dict[str, Game]:
    if not itad_ids:
        return {}
    rows = await session.scalars(select(Game).where(Game.itad_id.in_(list(itad_ids))))
    return {g.itad_id: g for g in rows if g.itad_id}


async def get_by_steam_app_id(session: AsyncSession, app_id: int) -> Game | None:
    return (await session.scalars(select(Game).where(Game.steam_app_id == app_id))).first()


async def upsert_provider_games(session: AsyncSession, games: Sequence[ProviderGame]) -> list[Game]:
    """Insert unseen provider games and refresh basic fields; preserves input order."""
    if not games:
        return []
    await insert_ignore(
        session,
        Game.__table__,
        [
            {
                "id": uuid.uuid4(),
                "itad_id": g.provider_id,
                "title": g.title,
                "slug": g.slug,
                "type": g.type,
                "mature": g.mature,
                "assets": g.assets,
                "developers": [],
                "publishers": [],
                "tags": [],
                "created_at": utcnow(),
                "updated_at": utcnow(),
            }
            for g in games
        ],
        ["itad_id"],
    )
    stored = await get_by_itad_ids(session, [g.provider_id for g in games])
    for g in games:
        row = stored[g.provider_id]
        row.title, row.slug, row.type, row.mature = g.title, g.slug, g.type, g.mature
        if g.assets:
            row.assets = g.assets
    return [stored[g.provider_id] for g in games]


async def apply_info(session: AsyncSession, game: Game, info: ProviderGameInfo) -> None:
    game.title, game.slug, game.type, game.mature = info.title, info.slug, info.type, info.mature
    if info.assets:
        game.assets = info.assets
    game.early_access = info.early_access
    game.release_date = info.release_date
    game.developers = info.developers
    game.publishers = info.publishers
    game.tags = info.tags
    game.primary_publisher = info.publishers[0] if info.publishers else None
    game.primary_tag = info.tags[0] if info.tags else None
    game.provider_url = info.url
    game.info_fetched_at = utcnow()
    if info.steam_app_id is not None and game.steam_app_id != info.steam_app_id:
        # Steam app ids are unique; never steal one already attached to another game.
        owner = await get_by_steam_app_id(session, info.steam_app_id)
        if owner is None or owner.id == game.id:
            game.steam_app_id = info.steam_app_id


async def search_local(session: AsyncSession, query: str, limit: int) -> list[Game]:
    pattern = f"%{query.strip().lower()}%"
    stmt = (
        select(Game)
        .where(or_(Game.title.ilike(pattern), Game.slug.ilike(pattern)))
        .order_by(Game.title)
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def get_shop_by_slug(session: AsyncSession, slug: str) -> Shop | None:
    return (await session.scalars(select(Shop).where(Shop.slug == slug))).first()


async def upsert_shops(session: AsyncSession, shops: Sequence[ProviderShop]) -> None:
    existing = {s.provider_shop_id: s for s in await session.scalars(select(Shop))}
    taken = {s.slug for s in existing.values()}
    for shop in shops:
        row = existing.get(shop.provider_shop_id)
        if row is not None:
            row.name, row.is_active = shop.name, True
            continue
        slug = slugify(shop.name) or f"shop-{shop.provider_shop_id}"
        if slug in taken:
            slug = f"{slug}-{shop.provider_shop_id}"
        taken.add(slug)
        session.add(
            Shop(provider_shop_id=shop.provider_shop_id, name=shop.name, slug=slug, is_active=True)
        )
    await session.flush()
