from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import ConfigDict, Field

from app.core.timeutil import utcnow
from app.forecasting.buckets import BUCKET_NAMES
from app.models import (
    Forecast,
    Game,
    PriceObservation,
    Recommendation,
    RegionalGamePrice,
    SaleEvent,
)
from app.providers.pricing.base import ATTRIBUTION
from app.schemas.common import ESTIMATE_NOTICE, ApiModel, Attribution, Money, money


class GameOut(ApiModel):
    id: uuid.UUID
    itad_id: str | None = Field(description="IsThereAnyDeal game id.")
    steam_app_id: int | None
    title: str
    slug: str
    type: str | None
    mature: bool
    early_access: bool | None
    release_date: date | None
    developers: list[str]
    publishers: list[str]
    tags: list[str]
    assets: dict[str, str]
    provider_url: str | None


class PriceOut(ApiModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "game_id": "0b0f6c3e-7d0e-4f7e-9d43-0e9d1f0c2a11",
                "shop": "steam",
                "country": "IN",
                "currency": "INR",
                "price": {"amount_minor": 74900, "currency": "INR", "amount": "749.00"},
                "regular": {"amount_minor": 149900, "currency": "INR", "amount": "1499.00"},
                "discount_pct": 50,
                "historical_low": {"amount_minor": 37400, "currency": "INR", "amount": "374.00"},
                "historical_low_at": "2025-12-19T18:00:00Z",
                "url": "https://store.steampowered.com/app/000000/",
                "observed_at": "2026-10-01T17:00:00Z",
                "fetched_at": "2026-10-08T09:12:00Z",
                "is_stale": False,
                "attribution": dict(ATTRIBUTION),
            }
        }
    )

    game_id: uuid.UUID
    shop: str
    country: str
    currency: str
    price: Money
    regular: Money
    discount_pct: int
    historical_low: Money | None
    historical_low_at: datetime | None
    url: str | None = Field(description="Provider URL, passed through unmodified.")
    observed_at: datetime = Field(description="When the provider last saw this price change.")
    fetched_at: datetime
    is_stale: bool
    attribution: Attribution


def price_out(row: RegionalGamePrice, shop_slug: str, stale_after_seconds: int) -> PriceOut:
    low = money(row.historical_low_minor, row.currency)
    return PriceOut(
        game_id=row.game_id,
        shop=shop_slug,
        country=row.country,
        currency=row.currency,
        price=money(row.price_minor, row.currency),
        regular=money(row.regular_minor, row.currency),
        discount_pct=row.discount_pct,
        historical_low=low,
        historical_low_at=row.historical_low_at,
        url=row.url,
        observed_at=row.observed_at,
        fetched_at=row.fetched_at,
        is_stale=(utcnow() - row.fetched_at).total_seconds() > stale_after_seconds,
        attribution=Attribution(**ATTRIBUTION),
    )


class HistoryPoint(ApiModel):
    observed_at: datetime
    price: Money
    regular: Money
    discount_pct: int


def history_point(row: PriceObservation) -> HistoryPoint:
    return HistoryPoint(
        observed_at=row.observed_at,
        price=money(row.price_minor, row.currency),
        regular=money(row.regular_minor, row.currency),
        discount_pct=row.discount_pct,
    )


class HistoryPage(ApiModel):
    game_id: uuid.UUID
    shop: str
    country: str
    items: list[HistoryPoint] = Field(
        description="Price *changes* in chronological order (a step series, not samples)."
    )
    next_cursor: str | None
    attribution: Attribution


class SaleEventOut(ApiModel):
    started_at: datetime
    ended_at: datetime | None = Field(description="Null while the sale is still running.")
    regular: Money
    initial_price: Money
    min_price: Money
    max_discount_pct: int


def sale_event_out(row: SaleEvent) -> SaleEventOut:
    return SaleEventOut(
        started_at=row.started_at,
        ended_at=row.ended_at,
        regular=money(row.regular_minor, row.currency),
        initial_price=money(row.initial_price_minor, row.currency),
        min_price=money(row.min_price_minor, row.currency),
        max_discount_pct=row.max_discount_pct,
    )


class HorizonProbabilities(ApiModel):
    days_7: float = Field(ge=0, le=1)
    days_30: float = Field(ge=0, le=1)
    days_90: float = Field(ge=0, le=1)


class PriceInterval(ApiModel):
    lower: Money
    median: Money
    upper: Money
    coverage: float = Field(description="Nominal central coverage of [lower, upper].")


class LikelyWindow(ApiModel):
    start: date
    end: date


class ExplanationFactorOut(ApiModel):
    code: str
    direction: str = Field(description="BUY, WAIT or NEUTRAL.")
    importance: float
    text: str


class ForecastOutcome(ApiModel):
    sale_within_7d: bool | None
    sale_within_30d: bool | None
    sale_within_90d: bool | None
    max_discount_pct: int | None
    min_price: Money | None
    evaluated_at: datetime | None
    complete: bool


class ForecastOut(ApiModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "5a2f8f0e-51f0-4a53-8a36-2f1a6a3f7c10",
                "game_id": "0b0f6c3e-7d0e-4f7e-9d43-0e9d1f0c2a11",
                "shop": "steam",
                "country": "IN",
                "currency": "INR",
                "created_at": "2026-10-08T09:12:00Z",
                "cutoff_at": "2026-10-08T09:12:00Z",
                "method": "BASELINE",
                "model_version": "baseline-1",
                "feature_schema_version": "fs-1",
                "currently_on_sale": False,
                "sale_probability": {"days_7": 0.08, "days_30": 0.41, "days_90": 0.93},
                "new_sale_probability": {"days_7": 0.08, "days_30": 0.41, "days_90": 0.93},
                "discount_tier_probabilities": {
                    "LT_20": 0.02,
                    "20_TO_29": 0.03,
                    "30_TO_39": 0.05,
                    "40_TO_49": 0.08,
                    "50_TO_59": 0.62,
                    "60_TO_74": 0.17,
                    "75_PLUS": 0.03,
                },
                "most_likely_discount_tier": "50_TO_59",
                "expected_discount_pct": 54.2,
                "current_price": {"amount_minor": 149900, "currency": "INR", "amount": "1499.00"},
                "regular_price": {"amount_minor": 149900, "currency": "INR", "amount": "1499.00"},
                "predicted_sale_price": {
                    "lower": {"amount_minor": 50966, "currency": "INR", "amount": "509.66"},
                    "median": {"amount_minor": 68954, "currency": "INR", "amount": "689.54"},
                    "upper": {"amount_minor": 89940, "currency": "INR", "amount": "899.40"},
                    "coverage": 0.8,
                },
                "likely_window": {"start": "2026-11-26", "end": "2026-12-03"},
                "confidence_score": 0.71,
                "data_quality": "GOOD",
                "explanation_factors": [
                    {
                        "code": "RECENT_SALE_CADENCE",
                        "direction": "WAIT",
                        "importance": 0.31,
                        "text": "Sales have historically started every 45–60 days.",
                    },
                ],
                "outcome": None,
                "disclaimer": ESTIMATE_NOTICE,
            }
        }
    )

    id: uuid.UUID
    game_id: uuid.UUID
    shop: str
    country: str
    currency: str | None
    created_at: datetime
    cutoff_at: datetime = Field(description="No price data after this instant was used.")
    method: str = Field(description="BASELINE or ML: the method that actually produced this.")
    model_version: str
    feature_schema_version: str
    currently_on_sale: bool
    sale_probability: HorizonProbabilities = Field(
        description="Estimated probability the game is discounted at any point in the horizon."
    )
    new_sale_probability: HorizonProbabilities = Field(
        description="Estimated probability a new sale starts in the horizon."
    )
    discount_tier_probabilities: dict[str, float] = Field(
        description=f"Distribution over {list(BUCKET_NAMES)}, conditional on a sale."
    )
    most_likely_discount_tier: str
    expected_discount_pct: float
    current_price: Money | None
    regular_price: Money | None
    predicted_sale_price: PriceInterval | None = Field(
        description="Estimated sale-price interval, conditional on a sale occurring."
    )
    likely_window: LikelyWindow | None = Field(
        description="Only present when the evidence is concentrated enough to give one."
    )
    confidence_score: float = Field(ge=0, le=1)
    data_quality: str = Field(description="GOOD, LIMITED or INSUFFICIENT.")
    explanation_factors: list[ExplanationFactorOut]
    outcome: ForecastOutcome | None = Field(
        description="What actually happened, filled in once horizons expire."
    )
    disclaimer: str = ESTIMATE_NOTICE


def forecast_out(row: Forecast, shop_slug: str) -> ForecastOut:
    tiers: dict[str, float] = row.discount_class_probs
    interval = None
    if row.price_lower_minor is not None and row.currency:
        interval = PriceInterval(
            lower=money(row.price_lower_minor, row.currency),
            median=money(row.price_median_minor, row.currency),
            upper=money(row.price_upper_minor, row.currency),
            coverage=0.8,
        )
    outcome = None
    if row.evaluated_at is not None:
        outcome = ForecastOutcome(
            sale_within_7d=row.actual_sale_7d,
            sale_within_30d=row.actual_sale_30d,
            sale_within_90d=row.actual_sale_90d,
            max_discount_pct=row.actual_max_discount_pct,
            min_price=money(row.actual_min_price_minor, row.currency),
            evaluated_at=row.evaluated_at,
            complete=row.evaluation_complete,
        )
    return ForecastOut(
        id=row.id,
        game_id=row.game_id,
        shop=shop_slug,
        country=row.country,
        currency=row.currency,
        created_at=row.created_at,
        cutoff_at=row.cutoff_at,
        method=row.method,
        model_version=row.model_version,
        feature_schema_version=row.feature_schema_version,
        currently_on_sale=row.currently_on_sale,
        sale_probability=HorizonProbabilities(
            days_7=row.sale_prob_7d, days_30=row.sale_prob_30d, days_90=row.sale_prob_90d
        ),
        new_sale_probability=HorizonProbabilities(
            days_7=row.new_sale_prob_7d,
            days_30=row.new_sale_prob_30d,
            days_90=row.new_sale_prob_90d,
        ),
        discount_tier_probabilities=tiers,
        most_likely_discount_tier=max(tiers, key=lambda name: tiers[name]),
        expected_discount_pct=row.expected_discount_pct,
        current_price=money(row.current_price_minor, row.currency),
        regular_price=money(row.regular_price_minor, row.currency),
        predicted_sale_price=interval,
        likely_window=(
            LikelyWindow(start=row.window_start, end=row.window_end)
            if row.window_start and row.window_end
            else None
        ),
        confidence_score=row.confidence_score,
        data_quality=row.data_quality,
        explanation_factors=[ExplanationFactorOut(**f) for f in row.explanation_factors],
        outcome=outcome,
    )


class ForecastPage(ApiModel):
    items: list[ForecastOut]
    next_cursor: str | None


class RecommendationOut(ApiModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "f3b4b1d8-2f3b-4a0a-8d0e-6c7f0c3b9a55",
                "forecast_id": "5a2f8f0e-51f0-4a53-8a36-2f1a6a3f7c10",
                "action": "WAIT",
                "score": 0.64,
                "sale_probability": 0.72,
                "max_wait_days": 60,
                "selected_horizon_days": 30,
                "expected_future_price": {
                    "amount_minor": 91500,
                    "currency": "INR",
                    "amount": "915.00",
                },
                "expected_savings": {"amount_minor": 58400, "currency": "INR", "amount": "584.00"},
                "waiting_cost": {"amount_minor": 14990, "currency": "INR", "amount": "149.90"},
                "reason_codes": ["HIGH_SALE_PROBABILITY", "MEANINGFUL_EXPECTED_SAVINGS"],
                "summary": "There is an estimated 72% chance of a new sale starting within 60 "
                "days, with an expected saving of about INR 584.00. This is a statistical "
                "estimate, not a guarantee or financial advice.",
                "thresholds": {"wait_min_probability": 0.6, "ruleset_version": "rules-1"},
                "ruleset_version": "rules-1",
                "created_at": "2026-10-08T09:12:01Z",
                "is_estimate": True,
            }
        }
    )

    id: uuid.UUID
    forecast_id: uuid.UUID
    action: str = Field(description="BUY, WAIT or NEUTRAL.")
    score: float
    sale_probability: float = Field(
        description="Estimated probability of a new sale within max_wait_days."
    )
    max_wait_days: int
    selected_horizon_days: int
    expected_future_price: Money | None
    expected_savings: Money | None = Field(description="Estimated; may be zero or negative.")
    waiting_cost: Money | None = Field(description="Configured impatience cost assumption.")
    reason_codes: list[str]
    summary: str
    thresholds: dict[str, Any]
    ruleset_version: str
    created_at: datetime
    is_estimate: bool = True


def recommendation_out(row: Recommendation) -> RecommendationOut:
    def signed(amount: int | None) -> Money | None:
        if amount is None or not row.currency:
            return None
        sign = "-" if amount < 0 else ""
        base = money(abs(amount), row.currency)
        assert base is not None
        return Money(amount_minor=amount, currency=row.currency, amount=f"{sign}{base.amount}")

    return RecommendationOut(
        id=row.id,
        forecast_id=row.forecast_id,
        action=row.action,
        score=row.score,
        sale_probability=row.sale_probability,
        max_wait_days=row.max_wait_days,
        selected_horizon_days=row.selected_horizon_days,
        expected_future_price=money(row.expected_future_price_minor, row.currency),
        expected_savings=signed(row.expected_savings_minor),
        waiting_cost=money(row.waiting_cost_minor, row.currency),
        reason_codes=row.reason_codes,
        summary=row.summary,
        thresholds=row.thresholds,
        ruleset_version=row.ruleset_version,
        created_at=row.created_at,
    )


def game_out(game: Game) -> GameOut:
    return GameOut.model_validate(game)
