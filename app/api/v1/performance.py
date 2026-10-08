from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ContainerDep, CurrentUser, SessionDep
from app.core.errors import ValidationFailed
from app.forecasting.buckets import BUCKET_LABELS, BUCKETS
from app.forecasting.policy import RULESET_VERSION
from app.forecasting.types import BASELINE_VERSION, FEATURE_SCHEMA_VERSION, HORIZONS
from app.models.enums import Channel
from app.providers.pricing.base import ATTRIBUTION
from app.repositories.games import slugify
from app.schemas.common import ESTIMATE_NOTICE, Attribution
from app.schemas.performance import (
    ActiveModelOut,
    ByHorizonOut,
    CalibrationOut,
    MetaOut,
    ModelVersionOut,
    PerformanceSummary,
)
from app.services.performance import PerformanceService

router = APIRouter(tags=["model-performance"])
VersionQuery = Annotated[
    str | None, Query(max_length=64, description="Restrict to one model version.")
]


@router.get(
    "/model-performance/summary",
    response_model=PerformanceSummary,
    summary="Aggregate accuracy of matured forecasts",
)
async def performance_summary(
    user: CurrentUser,
    session: SessionDep,
    container: ContainerDep,
    model_version: VersionQuery = None,
) -> PerformanceSummary:
    return PerformanceSummary(**await PerformanceService(session, container).summary(model_version))


@router.get(
    "/model-performance/calibration",
    response_model=CalibrationOut,
    summary="Reliability bins: predicted probability vs observed sale rate",
)
async def performance_calibration(
    user: CurrentUser,
    session: SessionDep,
    container: ContainerDep,
    horizon_days: Annotated[int, Query(description="One of 7, 30 or 90.")] = 30,
    bins: Annotated[int, Query(ge=2, le=20)] = 10,
    model_version: VersionQuery = None,
) -> CalibrationOut:
    if horizon_days not in HORIZONS:
        raise ValidationFailed(
            "`horizon_days` must be one of the forecast horizons.",
            details={"allowed": list(HORIZONS)},
        )
    data = await PerformanceService(session, container).calibration(
        horizon_days, model_version, bins
    )
    return CalibrationOut(**data)


@router.get(
    "/model-performance/by-horizon",
    response_model=ByHorizonOut,
    summary="Brier score, log loss and ranking metrics per horizon",
)
async def performance_by_horizon(
    user: CurrentUser,
    session: SessionDep,
    container: ContainerDep,
    model_version: VersionQuery = None,
) -> ByHorizonOut:
    return ByHorizonOut(**await PerformanceService(session, container).by_horizon(model_version))


@router.get(
    "/model-versions/active",
    response_model=ActiveModelOut,
    summary="The model version currently used for forecasts",
)
async def active_model(
    user: CurrentUser, session: SessionDep, container: ContainerDep
) -> ActiveModelOut:
    row = await PerformanceService(session, container).active_model()
    return ActiveModelOut(
        active=ModelVersionOut.model_validate(row) if row else None,
        baseline_version=BASELINE_VERSION,
        ruleset_version=RULESET_VERSION,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
    )


@router.get(
    "/meta",
    response_model=MetaOut,
    tags=["system"],
    summary="Public client configuration: attribution, VAPID key, tiers and horizons",
)
async def meta(container: ContainerDep) -> MetaOut:
    settings = container.settings
    return MetaOut(
        app_name=settings.app_name,
        api_version="v1",
        attribution=Attribution(**ATTRIBUTION),
        disclaimer=ESTIMATE_NOTICE,
        default_country=settings.default_country,
        default_currency=settings.default_currency,
        supported_shops=[slugify(settings.steam_shop_name)],
        forecast_horizons_days=list(HORIZONS),
        discount_tiers=[
            {"code": name, "label": BUCKET_LABELS[name], "min_pct": lo, "max_pct": hi - 1}
            for name, lo, hi in BUCKETS
        ],
        channels={
            Channel.WEB_PUSH.value: settings.web_push_configured,
            Channel.EMAIL.value: Channel.EMAIL in container.notifiers,
            Channel.SMS.value: settings.sms_configured,
        },
        vapid_public_key=settings.vapid_public_key if settings.web_push_configured else None,
        ruleset_version=RULESET_VERSION,
        baseline_version=BASELINE_VERSION,
    )
