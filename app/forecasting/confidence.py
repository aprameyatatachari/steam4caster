"""Confidence and data-quality scoring.

Confidence is deliberately *not* the largest class probability. It combines how much
and how recent the evidence is, how complete the features are, how well the producing
method has been calibrated, how spread the predictive distribution is, and whether the
inputs resemble what the method was built on.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from app.forecasting.buckets import entropy_ratio
from app.forecasting.features.builder import missing_feature_rate
from app.forecasting.types import SeriesState
from app.models.enums import DataQuality

WEIGHTS = {
    "history": 0.35,
    "recency": 0.15,
    "completeness": 0.10,
    "sharpness": 0.15,
    "calibration": 0.15,
    "in_distribution": 0.10,
}
COLD_START_CAP = 0.45
INSUFFICIENT_CAP = 0.30
# Assumed calibration quality of the untrained baseline until measured outcomes exist.
BASELINE_CALIBRATION_QUALITY = 0.6


@dataclass(frozen=True)
class ConfidenceBreakdown:
    score: float
    data_quality: str
    components: dict[str, float]


def data_quality(state: SeriesState) -> str:
    if state.n_sales >= 4 and state.history_days >= 365:
        return DataQuality.GOOD
    if state.n_sales >= 1 and state.history_days >= 90:
        return DataQuality.LIMITED
    return DataQuality.INSUFFICIENT


def calibration_quality_from_ece(ece: float | None) -> float:
    """Map an expected calibration error to 0..1 (0.0 ECE -> 1.0, >=0.2 ECE -> 0.0)."""
    if ece is None or math.isnan(ece):
        return BASELINE_CALIBRATION_QUALITY
    return max(0.0, 1.0 - ece / 0.2)


def score_confidence(
    state: SeriesState,
    tier_probs: Mapping[str, float],
    *,
    calibration_quality: float = BASELINE_CALIBRATION_QUALITY,
    in_distribution: float = 1.0,
) -> ConfidenceBreakdown:
    history = 0.6 * min(1.0, state.n_sales / 6) + 0.4 * min(1.0, state.history_days / 730)
    recency = math.exp(-max(0.0, state.data_age_days) / 30.0)
    completeness = 1.0 - missing_feature_rate(state.features)
    sharpness = 1.0 - entropy_ratio(tier_probs)
    components = {
        "history": history,
        "recency": recency,
        "completeness": completeness,
        "sharpness": sharpness,
        "calibration": min(1.0, max(0.0, calibration_quality)),
        "in_distribution": min(1.0, max(0.0, in_distribution)),
    }
    score = sum(WEIGHTS[name] * value for name, value in components.items())
    quality = data_quality(state)
    if quality == DataQuality.INSUFFICIENT:
        score = min(score, INSUFFICIENT_CAP)
    elif state.n_sales < 2:
        score = min(score, COLD_START_CAP)
    return ConfidenceBreakdown(
        score=round(min(1.0, max(0.0, score)), 4),
        data_quality=quality,
        components={k: round(v, 4) for k, v in components.items()},
    )
