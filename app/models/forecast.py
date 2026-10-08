from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.db.base import Base
from app.db.types import JSONType, UTCDateTime
from app.models.enums import DataQuality, ForecastMethod, ModelStatus, RecommendationAction, sql_in


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (CheckConstraint(sql_in("status", ModelStatus), name="status_valid"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    training_cutoff: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    hyperparameters: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ModelStatus.CANDIDATE)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    retired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Forecast(Base):
    """Append-only forecast. Only the ``actual_*``/``evaluat*`` columns are ever updated."""

    __tablename__ = "forecasts"
    __table_args__ = (
        Index("ix_forecasts_series_created", "game_id", "shop_id", "country", "created_at"),
        Index("ix_forecasts_pending_evaluation", "evaluation_complete", "cutoff_at"),
        CheckConstraint(sql_in("method", ForecastMethod), name="method_valid"),
        CheckConstraint(sql_in("data_quality", DataQuality), name="data_quality_valid"),
        CheckConstraint(
            "sale_prob_7d BETWEEN 0 AND 1 AND sale_prob_30d BETWEEN 0 AND 1 "
            "AND sale_prob_90d BETWEEN 0 AND 1",
            name="probability_range",
        ),
        CheckConstraint(
            "price_lower_minor IS NULL OR (price_lower_minor >= 0 "
            "AND price_lower_minor <= price_median_minor "
            "AND price_median_minor <= price_upper_minor)",
            name="price_interval_ordered",
        ),
        CheckConstraint("confidence_score BETWEEN 0 AND 1", name="confidence_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    # No observation after this instant was visible to the forecast.
    cutoff_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)

    currently_on_sale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Probability that the game is discounted at any point inside the horizon.
    sale_prob_7d: Mapped[float] = mapped_column(Float, nullable=False)
    sale_prob_30d: Mapped[float] = mapped_column(Float, nullable=False)
    sale_prob_90d: Mapped[float] = mapped_column(Float, nullable=False)
    # Probability that a *new* sale starts inside the horizon (differs when on sale now).
    new_sale_prob_7d: Mapped[float] = mapped_column(Float, nullable=False)
    new_sale_prob_30d: Mapped[float] = mapped_column(Float, nullable=False)
    new_sale_prob_90d: Mapped[float] = mapped_column(Float, nullable=False)

    discount_class_probs: Mapped[dict[str, float]] = mapped_column(JSONType, nullable=False)
    expected_discount_pct: Mapped[float] = mapped_column(Float, nullable=False)
    current_price_minor: Mapped[int | None] = mapped_column(Integer)
    regular_price_minor: Mapped[int | None] = mapped_column(Integer)
    historical_low_minor: Mapped[int | None] = mapped_column(Integer)
    price_lower_minor: Mapped[int | None] = mapped_column(Integer)
    price_median_minor: Mapped[int | None] = mapped_column(Integer)
    price_upper_minor: Mapped[int | None] = mapped_column(Integer)
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)

    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    data_quality: Mapped[str] = mapped_column(String(16), nullable=False)
    explanation_factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL")
    )
    feature_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # Private: never returned by the public API.
    feature_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)

    # --- outcome fields (the only mutable part of a forecast) ---------------
    actual_sale_7d: Mapped[bool | None] = mapped_column(Boolean)
    actual_sale_30d: Mapped[bool | None] = mapped_column(Boolean)
    actual_sale_90d: Mapped[bool | None] = mapped_column(Boolean)
    actual_max_discount_pct: Mapped[int | None] = mapped_column(Integer)
    actual_min_price_minor: Mapped[int | None] = mapped_column(Integer)
    actual_min_price_30d_minor: Mapped[int | None] = mapped_column(Integer)
    evaluated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    evaluation_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_recommendations_forecast", "forecast_id", "max_wait_days"),
        Index("ix_recommendations_entry_created", "watchlist_entry_id", "created_at"),
        CheckConstraint(sql_in("action", RecommendationAction), name="action_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    forecast_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("forecasts.id", ondelete="CASCADE"), nullable=False
    )
    watchlist_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("watchlist_entries.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3))
    expected_savings_minor: Mapped[int | None] = mapped_column(Integer)
    expected_future_price_minor: Mapped[int | None] = mapped_column(Integer)
    waiting_cost_minor: Mapped[int | None] = mapped_column(Integer)
    max_wait_days: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    sale_probability: Mapped[float] = mapped_column(Float, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    ruleset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
