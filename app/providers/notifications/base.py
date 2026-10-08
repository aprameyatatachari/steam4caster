"""Notification provider boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.models.enums import Channel


@dataclass(frozen=True)
class PushTarget:
    endpoint: str
    p256dh: str
    auth: str


@dataclass(frozen=True)
class NotificationMessage:
    channel: Channel
    title: str
    body: str
    url: str | None = None
    # Exactly one destination is set, matching the channel.
    email: str | None = None
    phone: str | None = None
    push: PushTarget | None = None
    idempotency_key: str | None = None
    tag: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    provider_message_id: str | None = None
    # Sanitised, low-cardinality code; never raw provider text or destinations.
    error_code: str | None = None
    # Transient failures are retried with backoff; terminal ones are not.
    transient: bool = False
    # The destination itself is permanently gone (e.g. push 404/410) and must be removed.
    destination_gone: bool = False

    @classmethod
    def ok(cls, provider_message_id: str | None = None) -> DeliveryResult:
        return cls(success=True, provider_message_id=provider_message_id)

    @classmethod
    def retry(cls, error_code: str) -> DeliveryResult:
        return cls(success=False, error_code=error_code, transient=True)

    @classmethod
    def fail(cls, error_code: str, *, destination_gone: bool = False) -> DeliveryResult:
        return cls(success=False, error_code=error_code, destination_gone=destination_gone)


class NotificationProvider(Protocol):
    channel: Channel

    async def send(self, message: NotificationMessage) -> DeliveryResult: ...

    async def aclose(self) -> None: ...


class FakeNotificationProvider:
    """Deterministic in-memory provider for tests. Results can be scripted per call."""

    def __init__(self, channel: Channel) -> None:
        self.channel = channel
        self.sent: list[NotificationMessage] = []
        self.attempts: list[NotificationMessage] = []
        self.script: list[DeliveryResult] = []

    async def send(self, message: NotificationMessage) -> DeliveryResult:
        self.attempts.append(message)
        result = (
            self.script.pop(0) if self.script else DeliveryResult.ok(f"fake-{len(self.attempts)}")
        )
        if result.success:
            self.sent.append(message)
        return result

    async def aclose(self) -> None:
        return None
