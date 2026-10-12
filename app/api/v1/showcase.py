"""Public, read-only endpoints behind the landing page. No account is needed, so every
route is rate limited by client IP and limited to a short list of regions."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import ContainerDep, SessionDep, rate_limit_by_ip
from app.core.errors import ValidationFailed
from app.forecasting.types import HORIZONS
from app.providers.pricing.base import ATTRIBUTION
from app.schemas.catalog import (
    forecast_out,
    game_out,
    history_point,
    price_out,
    recommendation_out,
    sale_event_out,
)
from app.schemas.common import Attribution, CountryCode, money
from app.schemas.performance import CalibrationOut
from app.schemas.showcase import DealOut, DealsOut, SnapshotOut
from app.services.performance import PerformanceService
from app.services.showcase import SHOWCASE_SIZE, ShowcaseService

router = APIRouter(
    prefix="/public",
    tags=["public"],
    dependencies=[Depends(rate_limit_by_ip("public", "rate_limit_public_per_minute", 60))],
)

SHOWCASE_COUNTRIES = ("IN", "US", "GB", "DE", "FR", "BR", "CA", "AU", "JP")
SHOWCASE_WAIT_DAYS = (7, 14, 30, 60, 90)

ShowcaseCountry = Annotated[
    CountryCode, Query(description=f"One of {', '.join(SHOWCASE_COUNTRIES)}.")
]


def _country(value: str) -> str:
    if value not in SHOWCASE_COUNTRIES:
        raise ValidationFailed(
            "This region is not available without an account.",
            details={"supported_countries": list(SHOWCASE_COUNTRIES)},
        )
    return value


@router.get(
    "/deals",
    response_model=DealsOut,
    summary="The ten notable Steam sales running right now",
    description="Full games on Steam discounted by at least the configured cut, most "
    "popular first. Prices are the region's own. `expires_at` is null when the "
    "provider does not know when the sale ends.",
)
async def best_deals(
    session: SessionDep,
    container: ContainerDep,
    country: ShowcaseCountry = "IN",
) -> DealsOut:
    region = _country(country)
    deals = await ShowcaseService(session, container).best_deals(region, SHOWCASE_SIZE)
    items = []
    for game, deal in deals:
        price = money(deal.price.amount_minor, deal.price.currency)
        regular = money(deal.regular.amount_minor, deal.regular.currency)
        if price is None or regular is None:
            continue
        items.append(
            DealOut(
                game=game_out(game),
                price=price,
                regular=regular,
                discount_pct=deal.cut,
                expires_at=deal.expiry,
                url=deal.url,
            )
        )
    return DealsOut(
        country=region,
        min_discount_pct=container.settings.best_deals_min_discount_pct,
        items=items,
        attribution=Attribution(**ATTRIBUTION),
    )


@router.get(
    "/games/{game_id}/snapshot",
    response_model=SnapshotOut,
    summary="Price history, forecast and recommendation for one listed deal",
    description="Only games currently returned by `/public/deals` for the region can be "
    "read; any other game is `404`. "
    "Forecasts and recommendations are estimates, not guarantees or financial advice.",
)
async def snapshot(
    game_id: uuid.UUID,
    session: SessionDep,
    container: ContainerDep,
    country: ShowcaseCountry = "IN",
    max_wait_days: Annotated[
        int, Query(description=f"One of {', '.join(map(str, SHOWCASE_WAIT_DAYS))}.")
    ] = 30,
) -> SnapshotOut:
    region = _country(country)
    if max_wait_days not in SHOWCASE_WAIT_DAYS:
        raise ValidationFailed(
            "`max_wait_days` is not one of the supported values.",
            details={"supported": list(SHOWCASE_WAIT_DAYS)},
        )
    data = await ShowcaseService(session, container).snapshot(game_id, region, max_wait_days)
    slug = data["shop"].slug
    stale_after = container.settings.price_stale_after_seconds
    return SnapshotOut(
        game=game_out(data["game"]),
        country=region,
        price=price_out(data["price"], slug, stale_after) if data["price"] else None,
        history=[history_point(r) for r in data["history"]],
        sale_events=[sale_event_out(e) for e in data["sale_events"]],
        forecast=forecast_out(data["forecast"], slug),
        recommendation=recommendation_out(data["recommendation"]),
        attribution=Attribution(**ATTRIBUTION),
    )


@router.get(
    "/calibration",
    response_model=CalibrationOut,
    summary="Reliability of past forecasts: predicted probability vs what happened",
    description="Empty until forecasts are old enough to be scored.",
)
async def public_calibration(
    session: SessionDep,
    container: ContainerDep,
    horizon_days: Annotated[int, Query(description="One of 7, 30 or 90.")] = 30,
) -> CalibrationOut:
    if horizon_days not in HORIZONS:
        raise ValidationFailed("`horizon_days` must be 7, 30 or 90.")
    return CalibrationOut(**await PerformanceService(session, container).calibration(horizon_days))
