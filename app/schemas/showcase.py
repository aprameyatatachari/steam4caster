"""Schemas for the unauthenticated landing-page endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas.catalog import (
    ForecastOut,
    GameOut,
    HistoryPoint,
    PriceOut,
    RecommendationOut,
    SaleEventOut,
)
from app.schemas.common import ESTIMATE_NOTICE, ApiModel, Attribution, Money


class DealOut(ApiModel):
    game: GameOut
    price: Money
    regular: Money
    discount_pct: int
    expires_at: datetime | None = Field(
        description="When the provider expects the sale to end. Often unknown."
    )
    url: str | None = Field(description="Provider URL, passed through unmodified.")


class DealsOut(ApiModel):
    country: str
    min_discount_pct: int = Field(description="Every listed deal is at least this deep.")
    items: list[DealOut]
    attribution: Attribution


class SnapshotOut(ApiModel):
    """Everything the landing page needs to demonstrate one game, in one response."""

    game: GameOut
    country: str
    price: PriceOut | None = Field(description="Null when Steam has no price in this region.")
    history: list[HistoryPoint]
    sale_events: list[SaleEventOut]
    forecast: ForecastOut
    recommendation: RecommendationOut
    attribution: Attribution
    disclaimer: str = ESTIMATE_NOTICE
