"""Outbox delivery worker.

Rows are claimed with a compare-and-set (PENDING -> SENDING) so two workers never send
the same row. Transient failures are retried with exponential backoff and jitter;
terminal failures are not retried. Every attempt is recorded in
``notification_deliveries``.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.core.logging import get_logger
from app.core.metrics import JOB_LAG, NOTIFICATION_DELIVERIES
from app.core.timeutil import utcnow
from app.models import (
    NotificationDelivery,
    NotificationEvent,
    NotificationOutbox,
    User,
)
from app.models.enums import Channel, OutboxState
from app.providers.notifications.base import DeliveryResult, NotificationMessage, PushTarget
from app.services.notifications import active_subscriptions, ensure_preferences

logger = get_logger(__name__)

DIGEST_MAX_ITEMS = 10


@dataclass
class Outcome:
    success: bool
    transient: bool = False
    provider_message_id: str | None = None
    error_code: str | None = None
    attempts: list[tuple[DeliveryResult, uuid.UUID | None]] = field(default_factory=list)


def backoff_seconds(attempt: int, base: int, cap: int) -> float:
    """Exponential backoff with full jitter on the upper half of the delay."""
    delay = min(cap, base * 2 ** max(0, attempt - 1))
    return delay / 2 + random.uniform(0, delay / 2)


class OutboxDispatcher:
    def __init__(self, session: AsyncSession, container: Container) -> None:
        self.session = session
        self.container = container
        self.settings = container.settings

    async def _send(
        self, user: User, channel: Channel, title: str, body: str, url: str | None, key: str
    ) -> Outcome:
        provider = self.container.notifiers.get(channel)
        if provider is None:
            return Outcome(False, error_code="CHANNEL_NOT_CONFIGURED")

        async def attempt(message: NotificationMessage) -> DeliveryResult:
            try:
                return await provider.send(message)
            except Exception:
                logger.exception("notification provider raised", extra={"channel": channel.value})
                return DeliveryResult.retry("PROVIDER_EXCEPTION")

        if channel == Channel.WEB_PUSH:
            subscriptions = await active_subscriptions(self.session, user.id)
            if not subscriptions:
                return Outcome(False, error_code="PUSH_NO_SUBSCRIPTION")
            outcome = Outcome(False)
            cipher = self.container.cipher
            for sub in subscriptions:
                try:
                    target = PushTarget(
                        endpoint=cipher.decrypt(sub.endpoint_encrypted),
                        p256dh=cipher.decrypt(sub.p256dh_encrypted),
                        auth=cipher.decrypt(sub.auth_encrypted),
                    )
                except ValueError:
                    result = DeliveryResult.fail("PUSH_UNDECRYPTABLE", destination_gone=True)
                else:
                    result = await attempt(
                        NotificationMessage(
                            channel=channel, title=title, body=body, url=url, push=target,
                            idempotency_key=key, tag=key[:16],
                        )
                    )  # fmt: skip
                outcome.attempts.append((result, sub.id))
                if result.success:
                    sub.last_success_at = utcnow()
                elif result.destination_gone:
                    sub.revoked_at = utcnow()  # terminal 404/410: stop using it
            results = [r for r, _ in outcome.attempts]
            outcome.success = any(r.success for r in results)
            outcome.transient = not outcome.success and any(r.transient for r in results)
            first = next((r for r in results if r.success), results[0])
            outcome.provider_message_id = first.provider_message_id
            outcome.error_code = None if outcome.success else first.error_code
            return outcome

        if channel == Channel.EMAIL:
            message = NotificationMessage(
                channel=channel, title=title, body=body, url=url, email=user.email,
                idempotency_key=key,
            )  # fmt: skip
        else:
            prefs = await ensure_preferences(self.session, user)
            pref = prefs[Channel.SMS]
            if not (pref.destination and pref.destination_verified and pref.opted_in_at):
                return Outcome(False, error_code="SMS_NOT_VERIFIED")
            message = NotificationMessage(
                channel=channel, title=title, body=body, url=url, phone=pref.destination,
                idempotency_key=key,
            )  # fmt: skip
        result = await attempt(message)
        return Outcome(
            result.success, result.transient, result.provider_message_id, result.error_code,
            [(result, None)],
        )  # fmt: skip

    async def _claim(self, outbox_id: uuid.UUID, now: datetime) -> bool:
        claimed = await self.session.execute(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.id == outbox_id,
                NotificationOutbox.state == OutboxState.PENDING.value,
            )
            .values(
                state=OutboxState.SENDING.value,
                locked_at=now,
                attempt_count=NotificationOutbox.attempt_count + 1,
            )
        )
        await self.session.commit()
        return claimed.rowcount == 1  # type: ignore[attr-defined]

    def _finalize(self, row: NotificationOutbox, outcome: Outcome, now: datetime) -> None:
        for result, subscription_id in outcome.attempts:
            self.session.add(
                NotificationDelivery(
                    outbox_id=row.id,
                    channel=row.channel,
                    attempt_number=row.attempt_count,
                    success=result.success,
                    transient=result.transient,
                    provider_message_id=result.provider_message_id,
                    error_code=result.error_code,
                    push_subscription_id=subscription_id,
                    created_at=now,
                )
            )
            NOTIFICATION_DELIVERIES.labels(
                row.channel,
                "sent" if result.success else ("retry" if result.transient else "failed"),
            ).inc()
        row.locked_at = None
        if outcome.success:
            row.state, row.sent_at = OutboxState.SENT.value, now
            row.provider_message_id, row.error_code = outcome.provider_message_id, None
        elif outcome.transient and row.attempt_count < self.settings.outbox_max_attempts:
            row.state, row.error_code = OutboxState.PENDING.value, outcome.error_code
            row.next_attempt_at = now + timedelta(
                seconds=backoff_seconds(
                    row.attempt_count,
                    self.settings.outbox_backoff_base_seconds,
                    self.settings.outbox_backoff_max_seconds,
                )
            )
        else:
            row.state = OutboxState.FAILED.value
            row.error_code = outcome.error_code or "DELIVERY_FAILED"

    async def dispatch_one(
        self, outbox_id: uuid.UUID, *, now: datetime | None = None
    ) -> str | None:
        """Claim and deliver one outbox row. Returns its final state, or ``None`` if
        another worker already holds it."""
        now = now or utcnow()
        if not await self._claim(outbox_id, now):
            return None
        row = (
            await self.session.scalars(
                select(NotificationOutbox)
                .where(NotificationOutbox.id == outbox_id)
                .execution_options(populate_existing=True)
            )
        ).one()
        event = await self.session.get(NotificationEvent, row.event_id)
        user = await self.session.get(User, row.user_id)
        if event is None or user is None or not user.is_active:
            row.state, row.error_code, row.locked_at = OutboxState.FAILED.value, "ORPHANED", None
            await self.session.commit()
            return row.state
        outcome = await self._send(
            user, Channel(row.channel), event.title, event.body, event.url, row.idempotency_key
        )
        self._finalize(row, outcome, now)
        await self.session.commit()
        return row.state

    async def release_stale(self, now: datetime) -> int:
        """Return rows stuck in SENDING (crashed worker) to PENDING after a timeout."""
        cutoff = now - timedelta(seconds=self.settings.outbox_sending_timeout_seconds)
        released = await self.session.execute(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.state == OutboxState.SENDING.value,
                NotificationOutbox.locked_at < cutoff,
            )
            .values(state=OutboxState.PENDING.value, locked_at=None, next_attempt_at=now)
        )
        await self.session.commit()
        return int(released.rowcount or 0)  # type: ignore[attr-defined]

    async def dispatch_pending(
        self, *, now: datetime | None = None, limit: int = 200
    ) -> dict[str, int]:
        now = now or utcnow()
        await self.release_stale(now)
        due = list(
            (
                await self.session.execute(
                    select(NotificationOutbox.id, NotificationOutbox.next_attempt_at)
                    .where(
                        NotificationOutbox.state == OutboxState.PENDING.value,
                        NotificationOutbox.is_digest.is_(False),
                        NotificationOutbox.next_attempt_at <= now,
                    )
                    .order_by(NotificationOutbox.next_attempt_at)
                    .limit(limit)
                )
            ).all()
        )
        JOB_LAG.labels("dispatch_outbox").set((now - due[0][1]).total_seconds() if due else 0.0)
        counts: dict[str, int] = {}
        for outbox_id, _ in due:
            state = await self.dispatch_one(outbox_id, now=now) or "SKIPPED"
            counts[state] = counts.get(state, 0) + 1
        return counts

    async def send_digests(self, *, now: datetime | None = None, force: bool = False) -> int:
        """Send one combined message per user and channel for pending digest rows.

        Runs hourly; a user's digest goes out when their local time reaches the
        configured digest hour (``force`` ignores the hour, for tests and manual runs).
        """
        now = now or utcnow()
        rows = list(
            await self.session.scalars(
                select(NotificationOutbox)
                .where(
                    NotificationOutbox.state == OutboxState.PENDING.value,
                    NotificationOutbox.is_digest.is_(True),
                    NotificationOutbox.next_attempt_at <= now,
                )
                .order_by(NotificationOutbox.created_at)
            )
        )
        groups: dict[tuple[uuid.UUID, str], list[NotificationOutbox]] = {}
        for row in rows:
            groups.setdefault((row.user_id, row.channel), []).append(row)
        sent = 0
        for (user_id, channel), group in groups.items():
            user = await self.session.get(User, user_id)
            if user is None:
                continue
            prefs = await ensure_preferences(self.session, user)
            local_hour = now.astimezone(ZoneInfo(prefs[Channel(channel)].timezone)).hour
            if not force and local_hour != self.settings.digest_hour_local:
                continue
            claimed = [row for row in group if await self._claim(row.id, now)]
            if not claimed:
                continue
            for row in claimed:
                await self.session.refresh(row)
            events = [await self.session.get(NotificationEvent, row.event_id) for row in claimed]
            items = [e for e in events if e is not None]
            lines = [f"• {e.title} — {e.body}" for e in items[:DIGEST_MAX_ITEMS]]
            if len(items) > DIGEST_MAX_ITEMS:
                lines.append(f"…and {len(items) - DIGEST_MAX_ITEMS} more.")
            outcome = await self._send(
                user,
                Channel(channel),
                f"{self.settings.app_name} digest: {len(items)} alert(s)",
                "\n".join(lines),
                None,
                claimed[0].idempotency_key,
            )
            for index, row in enumerate(claimed):
                # Record provider attempts once (on the first row) to keep counts honest.
                self._finalize(
                    row, outcome if index == 0 else Outcome(
                        outcome.success, outcome.transient, outcome.provider_message_id,
                        outcome.error_code,
                    ), now,
                )  # fmt: skip
            await self.session.commit()
            sent += int(outcome.success)
        return sent
