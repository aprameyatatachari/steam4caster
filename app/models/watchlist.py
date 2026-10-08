from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.db.base import Base
from app.db.types import JSONType, UTCDateTime


class WatchlistEntry(Base):
    __tablename__ = "watchlist_entries"
    __table_args__ = (
        Index("uq_watchlist_entries_user_game", "user_id", "game_id", unique=True),
        Index("ix_watchlist_entries_series", "game_id", "country", "is_active"),
        CheckConstraint(
            "target_price_minor IS NULL OR target_price_minor >= 0", name="target_non_negative"
        ),
        CheckConstraint(
            "min_discount_pct IS NULL OR min_discount_pct BETWEEN 1 AND 100", name="discount_range"
        ),
        CheckConstraint(
            "max_wait_days IS NULL OR max_wait_days BETWEEN 1 AND 365", name="wait_range"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    # Currency of ``target_price_minor``; must match the regional price to be compared.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    target_price_minor: Mapped[int | None] = mapped_column(Integer)
    min_discount_pct: Mapped[int | None] = mapped_column(Integer)
    max_wait_days: Mapped[int | None] = mapped_column(Integer)
    historical_low_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_on_buy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    channels: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Last recommendation seen by alert evaluation; used to detect transitions to BUY.
    last_recommendation_action: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
