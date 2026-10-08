from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import insert_ignore
from app.forecasting.types import SALE_DERIVATION_VERSION, PricePoint, SaleEventData
from app.models import Game, IngestionWatermark, PriceObservation, RegionalGamePrice, SaleEvent

OBSERVATION_IDENTITY = [
    "game_id",
    "shop_id",
    "country",
    "observed_at",
    "price_minor",
    "regular_minor",
]


def _series(model: Any, game_id: uuid.UUID, shop_id: int, country: str) -> Any:
    return and_(model.game_id == game_id, model.shop_id == shop_id, model.country == country)


async def get_regional_price(
    session: AsyncSession, game_id: uuid.UUID, shop_id: int, country: str
) -> RegionalGamePrice | None:
    stmt = (
        select(RegionalGamePrice)
        .where(_series(RegionalGamePrice, game_id, shop_id, country))
        # Rows are written with a Core upsert, so always refresh the identity map.
        .execution_options(populate_existing=True)
    )
    return (await session.scalars(stmt)).first()


async def regional_prices_for(
    session: AsyncSession, keys: Sequence[tuple[uuid.UUID, str]], shop_id: int
) -> dict[tuple[uuid.UUID, str], RegionalGamePrice]:
    if not keys:
        return {}
    stmt = (
        select(RegionalGamePrice)
        .where(
            RegionalGamePrice.shop_id == shop_id,
            RegionalGamePrice.game_id.in_({k[0] for k in keys}),
        )
        .execution_options(populate_existing=True)
    )
    wanted = set(keys)
    return {
        (r.game_id, r.country): r
        for r in await session.scalars(stmt)
        if (r.game_id, r.country) in wanted
    }


async def insert_observations(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> int:
    return await insert_ignore(session, PriceObservation.__table__, rows, OBSERVATION_IDENTITY)


async def observed_low(
    session: AsyncSession, game_id: uuid.UUID, shop_id: int, country: str, currency: str
) -> tuple[int, datetime] | None:
    """Lowest observed price for the series in ``currency`` and when it was first seen."""
    stmt = (
        select(PriceObservation.price_minor, PriceObservation.observed_at)
        .where(
            _series(PriceObservation, game_id, shop_id, country),
            PriceObservation.currency == currency,
        )
        .order_by(PriceObservation.price_minor, PriceObservation.observed_at)
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    return (row[0], row[1]) if row else None


async def low_before(
    session: AsyncSession,
    game_id: uuid.UUID,
    shop_id: int,
    country: str,
    currency: str,
    before: datetime,
) -> int | None:
    stmt = select(func.min(PriceObservation.price_minor)).where(
        _series(PriceObservation, game_id, shop_id, country),
        PriceObservation.currency == currency,
        PriceObservation.observed_at < before,
    )
    return await session.scalar(stmt)


async def latest_observation(
    session: AsyncSession, game_id: uuid.UUID, shop_id: int, country: str
) -> PriceObservation | None:
    stmt = (
        select(PriceObservation)
        .where(_series(PriceObservation, game_id, shop_id, country))
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        .limit(1)
    )
    return (await session.scalars(stmt)).first()


async def has_observations_newer_than(
    session: AsyncSession,
    game_id: uuid.UUID,
    shop_id: int,
    country: str,
    *,
    observed_after: datetime,
    ingested_after: datetime,
) -> bool:
    """True if the series gained data a forecast made at those instants never saw:
    either a later price change, or older history that was only ingested afterwards."""
    stmt = (
        select(PriceObservation.id)
        .where(
            _series(PriceObservation, game_id, shop_id, country),
            or_(
                PriceObservation.observed_at > observed_after,
                PriceObservation.ingested_at > ingested_after,
            ),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def series_points(
    session: AsyncSession,
    game_id: uuid.UUID,
    shop_id: int,
    country: str,
    *,
    until: datetime | None = None,
) -> tuple[list[PricePoint], str | None]:
    """Chronological points in the series' *current* currency.

    When a region's currency changed over time, older observations in the previous
    currency are excluded rather than compared across currencies.
    """
    stmt = (
        select(
            PriceObservation.observed_at,
            PriceObservation.price_minor,
            PriceObservation.regular_minor,
            PriceObservation.discount_pct,
            PriceObservation.currency,
        )
        .where(_series(PriceObservation, game_id, shop_id, country))
        .order_by(PriceObservation.observed_at, PriceObservation.id)
    )
    if until is not None:
        stmt = stmt.where(PriceObservation.observed_at <= until)
    rows = (await session.execute(stmt)).all()
    if not rows:
        return [], None
    currency = rows[-1].currency
    return [PricePoint(r[0], r[1], r[2], r[3]) for r in rows if r.currency == currency], currency


async def list_observations(
    session: AsyncSession,
    game_id: uuid.UUID,
    shop_id: int,
    country: str,
    *,
    start: datetime | None,
    end: datetime | None,
    after: tuple[datetime, int] | None,
    limit: int,
) -> list[PriceObservation]:
    stmt = select(PriceObservation).where(_series(PriceObservation, game_id, shop_id, country))
    if start is not None:
        stmt = stmt.where(PriceObservation.observed_at >= start)
    if end is not None:
        stmt = stmt.where(PriceObservation.observed_at <= end)
    if after is not None:
        at, last_id = after
        stmt = stmt.where(
            or_(
                PriceObservation.observed_at > at,
                and_(PriceObservation.observed_at == at, PriceObservation.id > last_id),
            )
        )
    stmt = stmt.order_by(PriceObservation.observed_at, PriceObservation.id).limit(limit)
    return list(await session.scalars(stmt))


async def replace_sale_events(
    session: AsyncSession,
    game_id: uuid.UUID,
    shop_id: int,
    country: str,
    currency: str,
    events: Sequence[SaleEventData],
) -> None:
    """Rebuild the derived sale events for one series inside the caller's transaction."""
    await session.execute(delete(SaleEvent).where(_series(SaleEvent, game_id, shop_id, country)))
    session.add_all(
        SaleEvent(
            game_id=game_id,
            shop_id=shop_id,
            country=country,
            currency=currency,
            started_at=e.started_at,
            ended_at=e.ended_at,
            initial_price_minor=e.initial_price_minor,
            min_price_minor=e.min_price_minor,
            regular_minor=e.regular_minor,
            max_discount_pct=e.max_discount_pct,
            derivation_version=SALE_DERIVATION_VERSION,
        )
        for e in events
    )
    await session.flush()


async def list_sale_events(
    session: AsyncSession, game_id: uuid.UUID, shop_id: int, country: str
) -> list[SaleEvent]:
    stmt = (
        select(SaleEvent)
        .where(_series(SaleEvent, game_id, shop_id, country))
        .order_by(SaleEvent.started_at)
    )
    return list(await session.scalars(stmt))


async def cohort_events(
    session: AsyncSession,
    shop_id: int,
    country: str,
    *,
    publisher: str | None = None,
    primary_tag: str | None = None,
    limit: int = 5000,
) -> dict[uuid.UUID, list[tuple[datetime, int]]]:
    """Recent sale events grouped by game for a cohort (all games when no filter)."""
    stmt = (
        select(SaleEvent.game_id, SaleEvent.started_at, SaleEvent.max_discount_pct)
        .where(SaleEvent.shop_id == shop_id, SaleEvent.country == country)
        .order_by(SaleEvent.started_at.desc())
        .limit(limit)
    )
    if publisher is not None or primary_tag is not None:
        stmt = stmt.join(Game, Game.id == SaleEvent.game_id)
        if publisher is not None:
            stmt = stmt.where(Game.primary_publisher == publisher)
        if primary_tag is not None:
            stmt = stmt.where(Game.primary_tag == primary_tag)
    grouped: dict[uuid.UUID, list[tuple[datetime, int]]] = {}
    for game_id, started_at, discount in (await session.execute(stmt)).all():
        grouped.setdefault(game_id, []).append((started_at, discount))
    return grouped


async def get_watermark(
    session: AsyncSession, game_id: uuid.UUID, shop_id: int, country: str
) -> IngestionWatermark | None:
    stmt = select(IngestionWatermark).where(_series(IngestionWatermark, game_id, shop_id, country))
    return (await session.scalars(stmt)).first()


async def get_or_create_watermark(
    session: AsyncSession, game_id: uuid.UUID, shop_id: int, country: str
) -> IngestionWatermark:
    await insert_ignore(
        session,
        IngestionWatermark.__table__,
        [{"id": uuid.uuid4(), "game_id": game_id, "shop_id": shop_id, "country": country}],
        ["game_id", "shop_id", "country"],
    )
    watermark = await get_watermark(session, game_id, shop_id, country)
    assert watermark is not None
    return watermark
