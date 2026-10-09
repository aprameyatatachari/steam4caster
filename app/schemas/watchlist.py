from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import ConfigDict, Field

from app.models.enums import Channel
from app.schemas.catalog import GameOut, RecommendationOut, recommendation_out
from app.schemas.common import ESTIMATE_NOTICE, ApiModel, CountryCode, CurrencyCode, Money, money
from app.services.watchlist import CurrencySummary, EntryView


class WatchlistCreate(ApiModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "game_id": "0b0f6c3e-7d0e-4f7e-9d43-0e9d1f0c2a11",
                "country": "IN",
                "currency": "INR",
                "target_price_minor": 74900,
                "min_discount_pct": 50,
                "max_wait_days": 60,
                "historical_low_only": False,
                "notify_on_buy": True,
                "channels": ["WEB_PUSH", "EMAIL"],
            }
        }
    )

    game_id: uuid.UUID
    country: CountryCode | None = Field(default=None, description="Defaults to the user's country.")
    currency: CurrencyCode | None = Field(
        default=None, description="Currency of target_price_minor; must match the region's."
    )
    target_price_minor: int | None = Field(default=None, ge=0, le=2_000_000_000)
    min_discount_pct: int | None = Field(default=None, ge=1, le=100)
    max_wait_days: int | None = Field(default=None, ge=1, le=365)
    historical_low_only: bool = False
    notify_on_buy: bool = True
    channels: list[Channel] | None = None


class WatchlistUpdate(ApiModel):
    currency: CurrencyCode | None = None
    target_price_minor: int | None = Field(default=None, ge=0, le=2_000_000_000)
    min_discount_pct: int | None = Field(default=None, ge=1, le=100)
    max_wait_days: int | None = Field(default=None, ge=1, le=365)
    historical_low_only: bool | None = None
    notify_on_buy: bool | None = None
    channels: list[Channel] | None = None
    is_active: bool | None = None


class WishlistImportRequest(ApiModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"profile": "https://steamcommunity.com/id/yourname"}}
    )

    profile: str = Field(
        min_length=2,
        max_length=200,
        description="Steam profile link, custom URL name, or 17-digit Steam ID.",
    )


class WishlistImportOut(ApiModel):
    steam_id: str
    on_wishlist: int = Field(description="Games on the Steam wishlist.")
    added: int
    added_titles: list[str] = Field(description="Up to 20 of the titles added.")
    already_watching: int
    not_found: int = Field(description="Wishlist items the price provider does not know.")
    failed: int = Field(description="Items skipped because the price provider errored.")
    skipped_over_limit: int = Field(description="Items beyond the per-import limit.")


class EntryPrice(ApiModel):
    price: Money
    regular: Money
    discount_pct: int
    historical_low: Money | None
    observed_at: datetime
    url: str | None


class EntryForecast(ApiModel):
    forecast_id: uuid.UUID
    created_at: datetime
    sale_probability_7d: float
    sale_probability_30d: float
    sale_probability_90d: float
    expected_discount_pct: float
    likely_window_start: date | None
    likely_window_end: date | None
    confidence_score: float
    data_quality: str
    method: str


class WatchlistEntryOut(ApiModel):
    id: uuid.UUID
    game: GameOut
    country: str
    currency: str
    target_price: Money | None
    min_discount_pct: int | None
    max_wait_days: int | None
    historical_low_only: bool
    notify_on_buy: bool
    channels: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    current: EntryPrice | None = Field(description="Null until a regional price is known.")
    forecast: EntryForecast | None
    recommendation: RecommendationOut | None


def entry_out(view: EntryView) -> WatchlistEntryOut:
    entry, price, forecast = view.entry, view.price, view.forecast
    return WatchlistEntryOut(
        id=entry.id,
        game=GameOut.model_validate(view.game),
        country=entry.country,
        currency=entry.currency,
        target_price=money(entry.target_price_minor, entry.currency),
        min_discount_pct=entry.min_discount_pct,
        max_wait_days=entry.max_wait_days,
        historical_low_only=entry.historical_low_only,
        notify_on_buy=entry.notify_on_buy,
        channels=entry.channels,
        is_active=entry.is_active,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        current=EntryPrice(
            price=money(price.price_minor, price.currency),
            regular=money(price.regular_minor, price.currency),
            discount_pct=price.discount_pct,
            historical_low=money(price.historical_low_minor, price.currency),
            observed_at=price.observed_at,
            url=price.url,
        )
        if price
        else None,
        forecast=EntryForecast(
            forecast_id=forecast.id,
            created_at=forecast.created_at,
            sale_probability_7d=forecast.sale_prob_7d,
            sale_probability_30d=forecast.sale_prob_30d,
            sale_probability_90d=forecast.sale_prob_90d,
            expected_discount_pct=forecast.expected_discount_pct,
            likely_window_start=forecast.window_start,
            likely_window_end=forecast.window_end,
            confidence_score=forecast.confidence_score,
            data_quality=forecast.data_quality,
            method=forecast.method,
        )
        if forecast
        else None,
        recommendation=recommendation_out(view.recommendation) if view.recommendation else None,
    )


class ExpectedCost(ApiModel):
    horizon_days: int
    expected_total: Money
    expected_savings: Money
    is_estimate: bool = True


class CurrencyTotals(ApiModel):
    currency: str
    games_priced: int
    current_total: Money
    historical_low_total: Money = Field(
        description="Total if every game were bought at its lowest recorded regional price."
    )
    games_with_historical_low: int
    games_with_forecast: int
    expected: list[ExpectedCost] = Field(
        description="Modelled totals if each game is bought at its expected price within the "
        "horizon. Estimates only."
    )


class LikelySale(ApiModel):
    entry_id: uuid.UUID
    game_id: uuid.UUID
    title: str
    sale_probability_30d: float
    window_start: date | None
    window_end: date | None


class WatchlistSummary(ApiModel):
    entries: int
    totals: list[CurrencyTotals] = Field(
        description="One block per currency. Amounts are never summed across currencies."
    )
    recommendation_counts: dict[str, int]
    likely_on_sale_within_30d: list[LikelySale]
    disclaimer: str = ESTIMATE_NOTICE


def currency_totals(summary: CurrencySummary) -> CurrencyTotals:
    c = summary.currency
    return CurrencyTotals(
        currency=c,
        games_priced=summary.games_priced,
        current_total=money(summary.current_total_minor, c),
        historical_low_total=money(summary.historical_low_total_minor, c),
        games_with_historical_low=summary.games_with_historical_low,
        games_with_forecast=summary.games_with_forecast,
        expected=[
            ExpectedCost(
                horizon_days=h,
                expected_total=money(total, c),
                expected_savings=money(max(0, summary.current_total_minor - total), c),
            )
            for h, total in sorted(summary.expected_total_minor.items())
        ],
    )
