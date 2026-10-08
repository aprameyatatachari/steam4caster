"""Notification preferences, push subscriptions and delivery history."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.core.errors import FeatureUnavailable, NotFoundError, RateLimited, ValidationFailed
from app.core.kv import hit_rate_limit
from app.core.security import hash_token
from app.core.timeutil import utcnow, validate_timezone
from app.models import (
    NotificationEvent,
    NotificationOutbox,
    NotificationPreference,
    PushSubscription,
    User,
)
from app.models.enums import Channel, DeliveryMode, OutboxState, TriggerType
from app.providers.notifications.base import NotificationMessage
from app.providers.notifications.sms import normalize_e164

SMS_CODE_TTL_SECONDS = 600
_DEFAULT_ENABLED = {Channel.WEB_PUSH: True, Channel.EMAIL: True, Channel.SMS: False}


async def ensure_preferences(
    session: AsyncSession, user: User
) -> dict[Channel, NotificationPreference]:
    """One preference row per channel, created lazily with conservative defaults."""
    rows = {
        Channel(p.channel): p
        for p in await session.scalars(
            select(NotificationPreference).where(NotificationPreference.user_id == user.id)
        )
    }
    for channel in Channel:
        if channel not in rows:
            pref = NotificationPreference(
                user_id=user.id,
                channel=channel.value,
                enabled=_DEFAULT_ENABLED[channel],
                timezone=user.timezone,
                delivery_mode=DeliveryMode.IMMEDIATE.value,
            )
            session.add(pref)
            rows[channel] = pref
    await session.flush()
    return rows


async def active_subscriptions(session: AsyncSession, user_id: uuid.UUID) -> list[PushSubscription]:
    now = utcnow()
    stmt = select(PushSubscription).where(
        PushSubscription.user_id == user_id,
        PushSubscription.revoked_at.is_(None),
        or_(PushSubscription.expires_at.is_(None), PushSubscription.expires_at > now),
    )
    return list(await session.scalars(stmt))


async def usable_channels(
    session: AsyncSession, container: Container, user: User
) -> dict[Channel, NotificationPreference]:
    """Channels that are enabled *and* have a verified, deliverable destination."""
    settings = container.settings
    prefs = await ensure_preferences(session, user)
    usable: dict[Channel, NotificationPreference] = {}
    for channel, pref in prefs.items():
        if not pref.enabled or channel not in container.notifiers:
            continue
        if channel == Channel.EMAIL:
            if settings.require_verified_email_for_alerts and not user.email_verified:
                continue
        elif channel == Channel.WEB_PUSH:
            if not await active_subscriptions(session, user.id):
                continue
        elif channel == Channel.SMS and not (
            settings.sms_configured
            and pref.destination
            and pref.destination_verified
            and pref.opted_in_at is not None
        ):
            continue
        usable[channel] = pref
    return usable


class NotificationService:
    def __init__(self, session: AsyncSession, container: Container) -> None:
        self.session = session
        self.container = container
        self.settings = container.settings

    # --- preferences ---------------------------------------------------------

    async def get_preferences(self, user: User) -> list[NotificationPreference]:
        prefs = await ensure_preferences(self.session, user)
        await self.session.commit()
        return [prefs[c] for c in Channel]

    async def update_preferences(
        self, user: User, updates: list[dict[str, Any]]
    ) -> list[NotificationPreference]:
        prefs = await ensure_preferences(self.session, user)
        for update in updates:
            pref = prefs[Channel(update["channel"])]
            if "timezone" in update and update["timezone"] is not None:
                try:
                    pref.timezone = validate_timezone(update["timezone"])
                except ValueError as exc:
                    raise ValidationFailed(str(exc)) from exc
            for name in ("quiet_hours_start", "quiet_hours_end"):
                if name in update:
                    setattr(pref, name, update[name])
            if update.get("delivery_mode") is not None:
                pref.delivery_mode = DeliveryMode(update["delivery_mode"]).value
            if "enabled" in update and update["enabled"] is not None:
                enabling = bool(update["enabled"])
                if enabling and pref.channel == Channel.SMS:
                    if not self.settings.sms_configured:
                        raise FeatureUnavailable(
                            "SMS notifications are not enabled on this server."
                        )
                    if not (pref.destination and pref.destination_verified):
                        raise ValidationFailed("Verify a phone number before enabling SMS alerts.")
                    pref.opted_in_at = pref.opted_in_at or utcnow()
                pref.enabled = enabling
        await self.session.commit()
        return [prefs[c] for c in Channel]

    # --- SMS destination verification ---------------------------------------

    async def request_sms_verification(self, user: User, phone: str) -> None:
        if not self.settings.sms_configured:
            raise FeatureUnavailable("SMS notifications are not enabled on this server.")
        try:
            number = normalize_e164(phone, user.default_country)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        if await hit_rate_limit(self.container.kv, "sms-verify", str(user.id), 3600) > 3:
            raise RateLimited("Too many verification attempts. Try again later.")
        prefs = await ensure_preferences(self.session, user)
        pref = prefs[Channel.SMS]
        pref.destination, pref.destination_verified, pref.enabled = number, False, False
        await self.session.commit()
        code = f"{secrets.randbelow(1_000_000):06d}"
        await self.container.kv.set(
            f"sms-code:{user.id}", f"{hash_token(code)}:{number}", SMS_CODE_TTL_SECONDS
        )
        await self.container.notifiers[Channel.SMS].send(
            NotificationMessage(
                channel=Channel.SMS,
                title=self.settings.app_name,
                body=f"Your verification code is {code}. It expires in 10 minutes.",
                phone=number,
            )
        )

    async def confirm_sms_verification(self, user: User, code: str) -> None:
        if await hit_rate_limit(self.container.kv, "sms-confirm", str(user.id), 600) > 5:
            raise RateLimited("Too many verification attempts. Try again later.")
        stored = await self.container.kv.get(f"sms-code:{user.id}")
        prefs = await ensure_preferences(self.session, user)
        pref = prefs[Channel.SMS]
        expected = f"{hash_token(code.strip())}:{pref.destination}"
        if stored is None or not secrets.compare_digest(stored, expected):
            raise ValidationFailed("The verification code is incorrect or has expired.")
        await self.container.kv.delete(f"sms-code:{user.id}")
        pref.destination_verified = True
        # Verifying a number is explicit opt-in; the user still has to enable the channel.
        pref.opted_in_at = utcnow()
        await self.session.commit()

    # --- push subscriptions --------------------------------------------------

    async def register_push_subscription(
        self,
        user: User,
        *,
        endpoint: str,
        p256dh: str,
        auth: str,
        device_label: str | None,
        expires_at: datetime | None,
    ) -> PushSubscription:
        parts = urlsplit(endpoint)
        if parts.scheme != "https" or not parts.hostname:
            raise ValidationFailed("Push endpoints must be https URLs.")
        cipher = self.container.cipher
        fingerprint = hash_token(endpoint)
        row = (
            await self.session.scalars(
                select(PushSubscription).where(PushSubscription.endpoint_fingerprint == fingerprint)
            )
        ).first()
        if row is None:
            row = PushSubscription(endpoint_fingerprint=fingerprint, user_id=user.id)
            self.session.add(row)
        # A browser re-subscribing (possibly under another account) takes the endpoint over.
        row.user_id = user.id
        row.endpoint_encrypted = cipher.encrypt(endpoint)
        row.endpoint_host = parts.hostname
        row.p256dh_encrypted = cipher.encrypt(p256dh)
        row.auth_encrypted = cipher.encrypt(auth)
        row.device_label = device_label
        row.expires_at = expires_at
        row.revoked_at = None
        await self.session.commit()
        return row

    async def list_push_subscriptions(self, user: User) -> list[PushSubscription]:
        return await active_subscriptions(self.session, user.id)

    async def revoke_push_subscription(self, user: User, subscription_id: uuid.UUID) -> None:
        row = await self.session.get(PushSubscription, subscription_id)
        if row is None or row.user_id != user.id:
            raise NotFoundError("Push subscription not found.")
        row.revoked_at = utcnow()
        await self.session.commit()

    # --- history and test ----------------------------------------------------

    async def history(
        self, user: User, *, before: tuple[datetime, str] | None, limit: int
    ) -> list[tuple[NotificationOutbox, NotificationEvent]]:
        stmt = (
            select(NotificationOutbox, NotificationEvent)
            .join(NotificationEvent, NotificationEvent.id == NotificationOutbox.event_id)
            .where(NotificationOutbox.user_id == user.id)
        )
        if before is not None:
            at, last_id = before
            stmt = stmt.where(
                or_(
                    NotificationOutbox.created_at < at,
                    and_(
                        NotificationOutbox.created_at == at,
                        NotificationOutbox.id < uuid.UUID(last_id),
                    ),
                )
            )
        stmt = stmt.order_by(
            NotificationOutbox.created_at.desc(), NotificationOutbox.id.desc()
        ).limit(limit)
        return [(row[0], row[1]) for row in (await self.session.execute(stmt)).all()]

    async def send_test(self, user: User) -> list[NotificationOutbox]:
        """Queue and immediately attempt one test notification per usable channel."""
        from app.services.dispatcher import OutboxDispatcher

        limit = self.settings.rate_limit_test_notification_per_hour
        if await hit_rate_limit(self.container.kv, "test-notification", str(user.id), 3600) > limit:
            raise RateLimited("Test notification limit reached. Try again later.")
        channels = await usable_channels(self.session, self.container, user)
        if not channels:
            raise ValidationFailed(
                "No notification channel is enabled with a verified destination."
            )
        event = NotificationEvent(
            user_id=user.id,
            event_type=TriggerType.TEST.value,
            trigger_identity=f"test:{uuid.uuid4()}",
            title=f"{self.settings.app_name} test notification",
            body="Notifications are working for this channel.",
            payload={},
        )
        self.session.add(event)
        await self.session.flush()
        rows = [
            NotificationOutbox(
                event_id=event.id,
                user_id=user.id,
                channel=channel.value,
                idempotency_key=hash_token(f"{event.trigger_identity}|{channel.value}"),
                state=OutboxState.PENDING.value,
            )
            for channel in channels
        ]
        self.session.add_all(rows)
        await self.session.commit()
        dispatcher = OutboxDispatcher(self.session, self.container)
        for row in rows:
            await dispatcher.dispatch_one(row.id)
        return rows

    async def count_recent(self, user_id: uuid.UUID, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(NotificationOutbox)
            .where(
                NotificationOutbox.user_id == user_id,
                NotificationOutbox.created_at >= since,
                NotificationOutbox.state != OutboxState.SUPPRESSED.value,
            )
        )
        return int(await self.session.scalar(stmt) or 0)
