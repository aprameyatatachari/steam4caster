from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.schemas.common import ApiModel, Attribution


class HorizonMetrics(ApiModel):
    horizon_days: int
    n: int
    positive_rate: float | None = None
    brier: float | None = None
    log_loss: float | None = None
    ece: float | None = Field(default=None, description="Expected calibration error.")
    roc_auc: float | None = None
    pr_auc: float | None = None
    threshold: float | None = None
    precision_at_threshold: float | None = None
    recall_at_threshold: float | None = None


class ByHorizonOut(ApiModel):
    model_version: str | None
    horizons: list[HorizonMetrics]


class CalibrationBin(ApiModel):
    bin_lower: float
    bin_upper: float
    count: int
    mean_predicted: float
    observed_rate: float


class CalibrationOut(ApiModel):
    model_version: str | None
    horizon_days: int
    n: int
    bins: list[CalibrationBin]


class PerformanceSummary(ApiModel):
    model_version: str | None
    forecasts_total: int
    forecasts_evaluated: int
    sale_probability: dict[str, dict[str, Any]]
    by_method: dict[str, dict[str, Any]]
    discount_tier: dict[str, Any]
    recommendation_policy: dict[str, Any]
    disclaimer: str


class ModelVersionOut(ApiModel):
    id: uuid.UUID
    name: str
    version: str
    status: str
    training_cutoff: datetime
    feature_schema_version: str
    hyperparameters: dict[str, Any]
    metrics: dict[str, Any]
    created_at: datetime
    activated_at: datetime | None


class ActiveModelOut(ApiModel):
    active: ModelVersionOut | None = Field(
        description="Null when no ML model is active and the deterministic baseline is in use."
    )
    fallback_method: str = "BASELINE"
    baseline_version: str
    ruleset_version: str
    feature_schema_version: str


class MetaOut(ApiModel):
    app_name: str
    api_version: str
    attribution: Attribution
    disclaimer: str
    default_country: str
    default_currency: str
    supported_shops: list[str]
    forecast_horizons_days: list[int]
    discount_tiers: list[dict[str, Any]]
    channels: dict[str, bool]
    vapid_public_key: str | None
    ruleset_version: str
    baseline_version: str
