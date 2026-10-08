from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any

from pydantic import ConfigDict, Field

from app.models.enums import Channel, DeliveryMode
from app.schemas.common import ApiModel


class PreferenceOut(ApiModel):
    channel: str
    enabled: bool
    available: bool = Field(description="Whether the server has this channel configured.")
    quiet_hours_start: time | None
    quiet_hours_end: time | None
    timezone: str
    delivery_mode: str
    destination_masked: str | None = Field(
        description="Masked destination for SMS; email and push use the account/browser."
    )
    destination_verified: bool


class PreferenceUpdate(ApiModel):
    channel: Channel
    enabled: bool | None = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    timezone: str | None = Field(default=None, max_length=64)
    delivery_mode: DeliveryMode | None = None


class PreferencesPatch(ApiModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "preferences": [
                    {
                        "channel": "EMAIL",
                        "enabled": True,
                        "delivery_mode": "DIGEST",
                        "quiet_hours_start": "22:00:00",
                        "quiet_hours_end": "07:00:00",
                        "timezone": "Asia/Kolkata",
                    }
                ]
            }
        }
    )

    preferences: list[PreferenceUpdate] = Field(min_length=1, max_length=3)


class PreferencesOut(ApiModel):
    preferences: list[PreferenceOut]
    vapid_public_key: str | None = Field(
        description="applicationServerKey for PushManager.subscribe(); null if push is off."
    )


class PushKeys(ApiModel):
    p256dh: str = Field(min_length=20, max_length=256)
    auth: str = Field(min_length=8, max_length=128)


class PushSubscriptionCreate(ApiModel):
    """Mirrors the browser's ``PushSubscription.toJSON()`` output."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {"p256dh": "BNc...base64url...", "auth": "tBH...base64url..."},
                "expirationTime": None,
                "device_label": "Chrome on Windows",
            }
        }
    )

    endpoint: str = Field(min_length=10, max_length=2048)
    keys: PushKeys
    expiration_time: datetime | None = Field(default=None, alias="expirationTime")
    device_label: str | None = Field(default=None, max_length=128)


class PushSubscriptionOut(ApiModel):
    id: uuid.UUID
    endpoint_host: str
    device_label: str | None
    created_at: datetime
    last_success_at: datetime | None
    expires_at: datetime | None


class SmsVerifyRequest(ApiModel):
    phone: str = Field(min_length=6, max_length=32, description="E.164 preferred.")


class SmsConfirmRequest(ApiModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class NotificationOut(ApiModel):
    id: uuid.UUID
    event_type: str
    channel: str
    state: str
    title: str
    body: str
    url: str | None
    game_id: uuid.UUID | None
    watchlist_entry_id: uuid.UUID | None
    error_code: str | None
    attempt_count: int
    created_at: datetime
    sent_at: datetime | None
    payload: dict[str, Any]


class NotificationPage(ApiModel):
    items: list[NotificationOut]
    next_cursor: str | None


class TestNotificationResult(ApiModel):
    channel: str
    state: str
    error_code: str | None


class TestNotificationOut(ApiModel):
    results: list[TestNotificationResult]
