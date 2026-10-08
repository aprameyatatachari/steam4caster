"""Online inference: ML when a compatible artifact is active, otherwise the baseline.

Inference never trains. Every forecast records the method that actually produced it.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import numpy as np

from app.core.logging import get_logger
from app.core.metrics import FORECASTS, INFERENCE_LATENCY, MODEL_FALLBACKS
from app.forecasting.baseline import (
    baseline_new_sale_probs,
    baseline_tier_probs,
    derive_window,
    enforce_monotonic,
)
from app.forecasting.buckets import BUCKET_NAMES, expected_discount, normalize, price_interval
from app.forecasting.confidence import (
    BASELINE_CALIBRATION_QUALITY,
    calibration_quality_from_ece,
    score_confidence,
)
from app.forecasting.explanations import FEATURE_GROUPS, build_factors
from app.forecasting.inference.registry import LoadedModel
from app.forecasting.types import BASELINE_VERSION, HORIZONS, ForecastResult, SeriesState
from app.models.enums import DataQuality, ForecastMethod

logger = get_logger(__name__)

ML_TIER_WEIGHT = 0.8  # the remainder keeps every tier possible via the baseline


def feature_vector(features: Mapping[str, float | None], names: list[str]) -> np.ndarray:
    return np.array(
        [[np.nan if features.get(n) is None else float(features[n]) for n in names]],  # type: ignore[arg-type]
        dtype=float,
    )


def in_distribution_score(
    features: Mapping[str, float | None], ranges: Mapping[str, tuple[float, float]]
) -> float:
    checked = outside = 0
    for name, (low, high) in ranges.items():
        value = features.get(name)
        if value is None:
            continue
        checked += 1
        span = max(high - low, 1e-9)
        if value < low - 0.1 * span or value > high + 0.1 * span:
            outside += 1
    return 1.0 - outside / checked if checked else 1.0


def _ml_predict(
    model: LoadedModel, state: SeriesState, baseline_tiers: dict[str, float]
) -> tuple[dict[int, float], dict[str, float], dict[str, float]]:
    x = feature_vector(state.features, model.feature_names)
    probs: dict[int, float] = {}
    for horizon in HORIZONS:
        raw = float(model.sale_models[horizon].predict_proba(x)[0, 1])
        probs[horizon] = float(model.calibrators[horizon].predict([raw])[0])
    tiers = baseline_tiers
    if model.tier_model is not None and model.tier_classes:
        predicted = model.tier_model.predict_proba(x)[0]
        ml_tiers = dict.fromkeys(BUCKET_NAMES, 0.0)
        for name, value in zip(model.tier_classes, predicted, strict=True):
            ml_tiers[name] = float(value)
        tiers = normalize(
            {
                name: ML_TIER_WEIGHT * ml_tiers[name] + (1 - ML_TIER_WEIGHT) * baseline_tiers[name]
                for name in BUCKET_NAMES
            }
        )
    contributions = model.sale_models[30].booster_.predict(x, pred_contrib=True)[0][:-1]
    by_feature = dict(zip(model.feature_names, contributions, strict=True))
    groups = {
        group: float(sum(by_feature.get(name, 0.0) for name in members))
        for group, members in FEATURE_GROUPS.items()
    }
    return enforce_monotonic(probs), tiers, groups


def forecast_series(state: SeriesState, model: LoadedModel | None = None) -> ForecastResult:
    """Produce a forecast for one series state."""
    started = time.perf_counter()
    baseline_probs = baseline_new_sale_probs(state)
    baseline_tiers = baseline_tier_probs(state)
    new_probs, tiers = baseline_probs, baseline_tiers
    method: str = ForecastMethod.BASELINE
    version = BASELINE_VERSION
    version_id: Any | None = None
    calibration = BASELINE_CALIBRATION_QUALITY
    in_dist = 1.0
    groups: dict[str, float] | None = None
    fallback_reason: str | None = None

    preliminary = score_confidence(state, baseline_tiers)
    if model is None:
        fallback_reason = "no_active_model"
    elif preliminary.data_quality == DataQuality.INSUFFICIENT:
        # Cold start: the hierarchical prior is more defensible than extrapolating a
        # model to a series it has essentially no evidence about.
        fallback_reason = "insufficient_history"
    else:
        try:
            new_probs, tiers, groups = _ml_predict(model, state, baseline_tiers)
            method, version, version_id = ForecastMethod.ML, model.version, model.version_id
            calibration = calibration_quality_from_ece(model.ece.get(30))
            in_dist = in_distribution_score(state.features, model.feature_ranges)
        except Exception:
            logger.exception("ml inference failed; using baseline")
            MODEL_FALLBACKS.labels("inference_error").inc()
            fallback_reason = "inference_error"
            new_probs, tiers, groups = baseline_probs, baseline_tiers, None

    confidence = score_confidence(
        state, tiers, calibration_quality=calibration, in_distribution=in_dist
    )
    # "Any discount inside the horizon" is certain while a sale is already running.
    sale_probs = dict.fromkeys(HORIZONS, 1.0) if state.on_sale else dict(new_probs)

    regular = state.current.regular_minor if state.current else None
    lower = median = upper = None
    if regular is not None and regular > 0:
        lower, median, upper = price_interval(regular, tiers)

    factors = build_factors(state, new_probs, tiers, ml_group_attributions=groups)
    snapshot: dict[str, Any] = {
        "features": state.features,
        "prior": {
            "level": state.prior.level,
            "median_interval_days": state.prior.median_interval_days,
            "n_events": state.prior.n_events,
        },
        "confidence_components": confidence.components,
        "baseline_new_sale_probs": {str(h): round(p, 6) for h, p in baseline_probs.items()},
        "fallback_reason": fallback_reason,
    }
    INFERENCE_LATENCY.labels(method).observe(time.perf_counter() - started)
    FORECASTS.labels(method).inc()
    return ForecastResult(
        method=method,
        model_version=version,
        model_version_id=version_id,
        cutoff=state.cutoff,
        currently_on_sale=state.on_sale,
        sale_probs={h: round(p, 6) for h, p in sale_probs.items()},
        new_sale_probs={h: round(p, 6) for h, p in new_probs.items()},
        tier_probs={k: round(v, 6) for k, v in tiers.items()},
        expected_discount_pct=round(expected_discount(tiers), 2),
        current_price_minor=state.current.price_minor if state.current else None,
        regular_price_minor=regular,
        historical_low_minor=state.historical_low_minor,
        price_lower_minor=lower,
        price_median_minor=median,
        price_upper_minor=upper,
        window=derive_window(state, new_probs),
        confidence=confidence.score,
        data_quality=confidence.data_quality,
        factors=factors,
        feature_snapshot=snapshot,
    )
