"""Pure domain types shared by feature building, the baseline, ML inference and policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.forecasting.calendar import SeasonalWindow

HORIZONS: tuple[int, ...] = (7, 30, 90)
SALE_DERIVATION_VERSION = "se-1"
FEATURE_SCHEMA_VERSION = "fs-1"
BASELINE_VERSION = "baseline-1"


@dataclass(frozen=True)
class PricePoint:
    at: datetime
    price_minor: int
    regular_minor: int
    discount_pct: int


@dataclass(frozen=True)
class SaleEventData:
    started_at: datetime
    ended_at: datetime | None
    initial_price_minor: int
    min_price_minor: int
    regular_minor: int
    max_discount_pct: int


@dataclass(frozen=True)
class GameContext:
    release_date: date | None = None
    publisher: str | None = None
    primary_tag: str | None = None
    type: str | None = None


@dataclass(frozen=True)
class CohortPrior:
    """Cold-start prior from a cohort. ``level`` records which fallback tier supplied it."""

    level: str  # PUBLISHER | TAG | GLOBAL | GLOBAL_DEFAULT
    median_interval_days: float
    n_events: int
    tier_probs: dict[str, float]
    seasonal_participation: float


@dataclass
class SeriesState:
    """Everything known about one game/shop/country series as of ``cutoff``."""

    cutoff: datetime
    points: list[PricePoint]
    events: list[SaleEventData]
    intervals: list[float]
    discounts: list[int]
    on_sale: bool
    current: PricePoint | None
    historical_low_minor: int | None
    history_days: float
    elapsed_since_last_start: float | None
    seasonal_hits: int
    seasonal_seen: int
    next_seasonal: SeasonalWindow
    active_seasonal: SeasonalWindow | None
    prior: CohortPrior
    context: GameContext
    data_age_days: float
    features: dict[str, float | None] = field(default_factory=dict)

    @property
    def n_sales(self) -> int:
        return len(self.events)


@dataclass(frozen=True)
class ExplanationFactor:
    code: str
    direction: str  # BUY | WAIT | NEUTRAL
    importance: float
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "direction": self.direction,
            "importance": round(self.importance, 4),
            "text": self.text,
        }


@dataclass
class ForecastResult:
    method: str
    model_version: str
    model_version_id: Any | None
    cutoff: datetime
    currently_on_sale: bool
    sale_probs: dict[int, float]
    new_sale_probs: dict[int, float]
    tier_probs: dict[str, float]
    expected_discount_pct: float
    current_price_minor: int | None
    regular_price_minor: int | None
    historical_low_minor: int | None
    price_lower_minor: int | None
    price_median_minor: int | None
    price_upper_minor: int | None
    window: tuple[date, date] | None
    confidence: float
    data_quality: str
    factors: list[ExplanationFactor]
    feature_snapshot: dict[str, Any]
