from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.db.base import Base
from app.db.types import JSONType, UTCDateTime
from app.models.enums import Channel, DeliveryMode, OutboxState, sql_in


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "channel", name="uq_notification_preferences_user_channel"),
        CheckConstraint(sql_in("channel", Channel), name="channel_valid"),
        CheckConstraint(sql_in("delivery_mode", DeliveryMode), name="mode_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quiet_hours_start: Mapped[time | None] = mapped_column(Time)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    delivery_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DeliveryMode.IMMEDIATE
    )
    # Channel destination when it is not implied by the account (SMS phone number, E.164).
    destination: Mapped[str | None] = mapped_column(String(320))
    destination_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    opted_in_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    __table_args__ = (Index("ix_push_subscriptions_user", "user_id", "revoked_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # The endpoint is a capability URL: stored encrypted, identified by its fingerprint.
    endpoint_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    endpoint_host: Mapped[str] = mapped_column(String(255), nullable=False)
    p256dh_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    auth_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    device_label: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class NotificationEvent(Base):
    __tablename__ = "notification_events"
    __table_args__ = (
        Index("ix_notification_events_user_created", "user_id", "created_at"),
        Index(
            "ix_notification_events_entry_type", "watchlist_entry_id", "event_type", "created_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id", ondelete="SET NULL"))
    watchlist_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("watchlist_entries.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Stable identity of what triggered the event (price/sale/forecast identity).
    trigger_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        Index("ix_notification_outbox_pending", "state", "next_attempt_at"),
        Index("ix_notification_outbox_user_created", "user_id", "created_at"),
        CheckConstraint(sql_in("state", OutboxState), name="state_valid"),
        CheckConstraint(sql_in("channel", Channel), name="channel_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_events.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=OutboxState.PENDING)
    is_digest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class NotificationDelivery(Base):
    """One row per delivery attempt (per push subscription for Web Push)."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (Index("ix_notification_deliveries_outbox", "outbox_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    outbox_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_outbox.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    transient: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(64))
    push_subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("push_subscriptions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
