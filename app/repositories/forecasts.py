from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Forecast, Recommendation, WatchlistEntry


async def latest_forecast(
    session: AsyncSession, game_id: uuid.UUID, shop_id: int, country: str
) -> Forecast | None:
    stmt = (
        select(Forecast)
        .where(
            Forecast.game_id == game_id, Forecast.shop_id == shop_id, Forecast.country == country
        )
        .order_by(Forecast.created_at.desc(), Forecast.id.desc())
        .limit(1)
    )
    return (await session.scalars(stmt)).first()


async def latest_forecasts_for(
    session: AsyncSession, keys: Sequence[tuple[uuid.UUID, str]], shop_id: int
) -> dict[tuple[uuid.UUID, str], Forecast]:
    result: dict[tuple[uuid.UUID, str], Forecast] = {}
    for game_id, country in set(keys):
        forecast = await latest_forecast(session, game_id, shop_id, country)
        if forecast is not None:
            result[(game_id, country)] = forecast
    return result


async def forecast_history(
    session: AsyncSession,
    game_id: uuid.UUID,
    shop_id: int,
    country: str,
    *,
    before: tuple[datetime, str] | None,
    limit: int,
) -> list[Forecast]:
    stmt = select(Forecast).where(
        Forecast.game_id == game_id, Forecast.shop_id == shop_id, Forecast.country == country
    )
    if before is not None:
        at, last_id = before
        stmt = stmt.where(
            or_(
                Forecast.created_at < at,
                and_(Forecast.created_at == at, Forecast.id < uuid.UUID(last_id)),
            )
        )
    stmt = stmt.order_by(Forecast.created_at.desc(), Forecast.id.desc()).limit(limit)
    return list(await session.scalars(stmt))


async def find_recommendation(
    session: AsyncSession,
    forecast_id: uuid.UUID,
    max_wait_days: int,
    watchlist_entry_id: uuid.UUID | None,
) -> Recommendation | None:
    stmt = select(Recommendation).where(
        Recommendation.forecast_id == forecast_id, Recommendation.max_wait_days == max_wait_days
    )
    if watchlist_entry_id is None:
        stmt = stmt.where(Recommendation.watchlist_entry_id.is_(None))
    else:
        stmt = stmt.where(Recommendation.watchlist_entry_id == watchlist_entry_id)
    return (await session.scalars(stmt.order_by(Recommendation.created_at.desc()))).first()


async def watched_series(session: AsyncSession) -> list[tuple[uuid.UUID, str]]:
    """Distinct (game, country) pairs with at least one active watchlist entry."""
    stmt = (
        select(WatchlistEntry.game_id, WatchlistEntry.country)
        .where(WatchlistEntry.is_active.is_(True))
        .distinct()
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]
