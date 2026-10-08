from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.db.base import Base
from app.db.types import BigIntPK, JSONType, UTCDateTime


class Game(Base):
    __tablename__ = "games"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    itad_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    steam_app_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    type: Mapped[str | None] = mapped_column(String(32))
    mature: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    early_access: Mapped[bool | None] = mapped_column(Boolean)
    release_date: Mapped[date | None] = mapped_column(Date)
    developers: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    publishers: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    tags: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    # Denormalised cohort keys used for cold-start priors.
    primary_publisher: Mapped[str | None] = mapped_column(String(255), index=True)
    primary_tag: Mapped[str | None] = mapped_column(String(128), index=True)
    assets: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    provider_url: Mapped[str | None] = mapped_column(Text)
    info_fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_shop_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


_MONEY_CHECKS = (
    CheckConstraint("price_minor >= 0", name="price_non_negative"),
    CheckConstraint("regular_minor >= 0", name="regular_non_negative"),
    CheckConstraint("discount_pct BETWEEN 0 AND 100", name="discount_range"),
)


class RegionalGamePrice(Base):
    """Latest known price for one game in one shop and country (one row per region)."""

    __tablename__ = "regional_game_prices"
    __table_args__ = (
        UniqueConstraint("game_id", "shop_id", "country", name="uq_regional_game_prices_identity"),
        *_MONEY_CHECKS,
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    regular_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_pct: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    # Provider/affiliate URL, stored exactly as supplied.
    url: Mapped[str | None] = mapped_column(Text)
    historical_low_minor: Mapped[int | None] = mapped_column(Integer)
    historical_low_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class PriceObservation(Base):
    """Append-only price change log. Replays are absorbed by the identity constraint."""

    __tablename__ = "price_observations"
    __table_args__ = (
        UniqueConstraint(
            "game_id",
            "shop_id",
            "country",
            "observed_at",
            "price_minor",
            "regular_minor",
            name="uq_price_observations_identity",
        ),
        Index("ix_price_observations_series_time", "game_id", "shop_id", "country", "observed_at"),
        *_MONEY_CHECKS,
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    regular_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_pct: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="history")


class SaleEvent(Base):
    """Contiguous discounted period derived from observations (rebuildable)."""

    __tablename__ = "sale_events"
    __table_args__ = (
        UniqueConstraint(
            "game_id", "shop_id", "country", "started_at", name="uq_sale_events_identity"
        ),
        Index("ix_sale_events_cohort", "shop_id", "country", "started_at"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="end_after_start"),
        CheckConstraint("max_discount_pct BETWEEN 1 AND 100", name="discount_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    initial_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    min_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    regular_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    max_discount_pct: Mapped[int] = mapped_column(Integer, nullable=False)
    derivation_version: Mapped[str] = mapped_column(String(16), nullable=False)


class IngestionWatermark(Base):
    """Per game/shop/country history-ingestion progress, so backfills are restartable."""

    __tablename__ = "ingestion_watermarks"
    __table_args__ = (
        UniqueConstraint("game_id", "shop_id", "country", name="uq_ingestion_watermarks_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    # Earliest instant history has been requested from (backfill start).
    history_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Instant up to which history is known to be complete.
    covered_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    backfill_completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
