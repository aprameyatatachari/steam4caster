from __future__ import annotations

import smtplib
import uuid
from datetime import UTC, datetime, time, timedelta

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.core.container import Container
from app.core.timeutil import utcnow
from app.models import (
    Game,
    NotificationDelivery,
    NotificationEvent,
    NotificationOutbox,
    NotificationPreference,
    PushSubscription,
    User,
    WatchlistEntry,
)
from app.models.enums import Channel, OutboxState, TriggerType
from app.providers.notifications.base import DeliveryResult, NotificationMessage, PushTarget
from app.providers.notifications.email import ResendEmailProvider, SmtpEmailProvider
from app.providers.notifications.sms import DisabledSmsProvider, TwilioSmsProvider, normalize_e164
from app.providers.notifications.webpush import (
    WebPushProvider,
    build_payload,
    classify_push_status,
)
from app.services.alerts import AlertService, Trigger
from app.services.dispatcher import OutboxDispatcher
from tests.conftest import email_provider, push_provider, register

PUSH_BODY = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/device-capability-token-1",
    "keys": {"p256dh": "B" + "p" * 86, "auth": "a" * 22},
    "expirationTime": None,
    "device_label": "Chrome on Windows",
}


async def setup_entry(
    container: Container, email: str = "user@example.com"
) -> tuple[User, WatchlistEntry, Game]:
    async with container.db.session() as session:
        user = (await session.scalars(select(User).where(User.email == email))).one()
        game = Game(itad_id=str(uuid.uuid4()), title="Alert Game", slug="alert-game")
        session.add(game)
        await session.flush()
        entry = WatchlistEntry(
            user_id=user.id, game_id=game.id, country="US", currency="USD",
            channels=["EMAIL", "WEB_PUSH"],
        )  # fmt: skip
        session.add(entry)
        await session.commit()
        return user, entry, game


def trigger(identity: str = "1000@t1", kind: TriggerType = TriggerType.TARGET_PRICE) -> Trigger:
    return Trigger(kind, identity, "Alert Game is at or below your target price", "Now USD 10.00.")


async def raise_alert(
    container: Container, user: User, entry: WatchlistEntry, game: Game, **kw
) -> int:  # type: ignore[no-untyped-def]
    async with container.db.session() as session:
        return await AlertService(session, container).create_alert(
            await session.get(User, user.id),
            await session.get(WatchlistEntry, entry.id),
            game,  # type: ignore[arg-type]
            kw.pop("trigger", trigger()),
            url="https://example.invalid/x",
            now=kw.pop("now", utcnow()),
            channels=kw.pop("channels", ["EMAIL"]),
        )


async def outbox_rows(container: Container) -> list[NotificationOutbox]:
    async with container.db.session() as session:
        return list(
            await session.scalars(
                select(NotificationOutbox).order_by(NotificationOutbox.created_at)
            )
        )


async def dispatch(container: Container, **kwargs) -> dict[str, int]:  # type: ignore[no-untyped-def]
    async with container.db.session() as session:
        return await OutboxDispatcher(session, container).dispatch_pending(**kwargs)


# --- preferences and subscriptions API -------------------------------------


async def test_preferences_defaults_and_update(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    body = (await client.get("/api/v1/notification-preferences", headers=auth)).json()
    prefs = {p["channel"]: p for p in body["preferences"]}
    assert set(prefs) == {"WEB_PUSH", "EMAIL", "SMS"}
    assert prefs["EMAIL"]["enabled"] and prefs["WEB_PUSH"]["enabled"]
    assert not prefs["SMS"]["enabled"] and not prefs["SMS"]["available"]  # off by default
    assert body["vapid_public_key"] == "BPublicKeyForTests"

    patched = await client.patch(
        "/api/v1/notification-preferences",
        json={"preferences": [{"channel": "EMAIL", "delivery_mode": "DIGEST",
                               "quiet_hours_start": "22:00:00", "quiet_hours_end": "07:00:00",
                               "timezone": "Asia/Kolkata"}]},
        headers=auth,
    )  # fmt: skip
    assert patched.status_code == 200, patched.text
    email = {p["channel"]: p for p in patched.json()["preferences"]}["EMAIL"]
    assert email["delivery_mode"] == "DIGEST" and email["timezone"] == "Asia/Kolkata"
    assert email["quiet_hours_start"] == "22:00:00" and email["enabled"] is True

    bad_tz = await client.patch(
        "/api/v1/notification-preferences",
        json={"preferences": [{"channel": "EMAIL", "timezone": "Nowhere/Land"}]}, headers=auth,
    )  # fmt: skip
    assert bad_tz.status_code == 422
    sms = await client.patch(
        "/api/v1/notification-preferences",
        json={"preferences": [{"channel": "SMS", "enabled": True}]}, headers=auth,
    )  # fmt: skip
    assert sms.status_code == 503 and sms.json()["error"]["code"] == "FEATURE_UNAVAILABLE"
    verify = await client.post(
        "/api/v1/notification-preferences/sms/verify", json={"phone": "+14155550123"}, headers=auth
    )
    assert verify.status_code == 503


async def test_push_subscription_lifecycle_and_encryption(
    client: httpx.AsyncClient, container: Container
) -> None:
    alice = await register(client, "alice@example.com")
    mallory = await register(client, "mallory@example.com")
    created = await client.post("/api/v1/push-subscriptions", json=PUSH_BODY, headers=alice)
    assert created.status_code == 201, created.text
    sub = created.json()
    assert (
        sub["endpoint_host"] == "fcm.googleapis.com" and sub["device_label"] == "Chrome on Windows"
    )
    assert "device-capability-token-1" not in created.text and "p256dh" not in created.text

    async with container.db.session() as session:
        row = (await session.scalars(select(PushSubscription))).one()
        stored = f"{row.endpoint_encrypted}{row.p256dh_encrypted}{row.auth_encrypted}"
        assert "device-capability-token-1" not in stored and "ppppp" not in stored
        assert container.cipher.decrypt(row.endpoint_encrypted) == PUSH_BODY["endpoint"]

    # Re-registering the same endpoint updates it rather than duplicating it.
    again = await client.post("/api/v1/push-subscriptions", json=PUSH_BODY, headers=alice)
    assert again.json()["id"] == sub["id"]
    assert len((await client.get("/api/v1/push-subscriptions", headers=alice)).json()) == 1

    url = f"/api/v1/push-subscriptions/{sub['id']}"
    assert (await client.delete(url, headers=mallory)).status_code == 404  # not hers
    assert (await client.delete(url, headers=alice)).status_code == 204
    assert (await client.get("/api/v1/push-subscriptions", headers=alice)).json() == []
    insecure = await client.post(
        "/api/v1/push-subscriptions", json={**PUSH_BODY, "endpoint": "http://plain.example/x"},
        headers=alice,
    )  # fmt: skip
    assert insecure.status_code == 422


async def test_test_notification_is_strictly_rate_limited(
    client: httpx.AsyncClient, auth: dict[str, str], container: Container
) -> None:
    email_provider(container).sent.clear()
    first = await client.post("/api/v1/notifications/test", headers=auth)
    assert first.status_code == 200, first.text
    # No push subscription yet, so only email is usable.
    assert first.json()["results"] == [{"channel": "EMAIL", "state": "SENT", "error_code": None}]
    assert len(email_provider(container).sent) == 1
    assert (await client.post("/api/v1/notifications/test", headers=auth)).status_code == 200
    limited = await client.post("/api/v1/notifications/test", headers=auth)
    assert limited.status_code == 429  # limit of 2 per hour in test settings
    history = (await client.get("/api/v1/notifications", headers=auth)).json()
    assert [n["event_type"] for n in history["items"]] == ["TEST", "TEST"]
    assert history["items"][0]["state"] == "SENT"


# --- outbox: idempotency, cooldown, limits, quiet hours ---------------------


async def test_alert_is_created_once_and_delivered_once(
    client: httpx.AsyncClient, auth, container: Container
) -> None:  # type: ignore[no-untyped-def]
    user, entry, game = await setup_entry(container)
    email_provider(container).sent.clear()
    assert await raise_alert(container, user, entry, game) == 1
    # The same condition for the same identity never creates a second alert.
    assert await raise_alert(container, user, entry, game) == 0
    assert await raise_alert(container, user, entry, game) == 0
    assert len(await outbox_rows(container)) == 1

    assert await dispatch(container) == {"SENT": 1}
    assert await dispatch(container) == {}  # nothing left to send
    sent = email_provider(container).sent
    assert len(sent) == 1 and sent[0].email == "user@example.com"
    assert sent[0].idempotency_key == (await outbox_rows(container))[0].idempotency_key
    row = (await outbox_rows(container))[0]
    assert row.state == OutboxState.SENT and row.sent_at is not None and row.attempt_count == 1
    assert row.provider_message_id is not None


async def test_cooldown_suppresses_a_new_identity_of_the_same_trigger(
    client: httpx.AsyncClient,
    auth,
    container: Container,  # type: ignore[no-untyped-def]
) -> None:
    user, entry, game = await setup_entry(container)
    now = utcnow()
    await raise_alert(container, user, entry, game, trigger=trigger("1000@t1"), now=now)
    await raise_alert(container, user, entry, game, trigger=trigger("900@t2"),
                      now=now + timedelta(hours=1))  # fmt: skip
    other = trigger("50@t2", TriggerType.MIN_DISCOUNT)  # a different trigger type is separate
    await raise_alert(container, user, entry, game, trigger=other, now=now + timedelta(hours=1))
    after = now + timedelta(hours=container.settings.alert_cooldown_hours + 1)
    await raise_alert(container, user, entry, game, trigger=trigger("800@t3"), now=after)
    rows = await outbox_rows(container)
    assert [(r.state, r.error_code) for r in rows] == [
        ("PENDING", None), ("SUPPRESSED", "COOLDOWN"), ("PENDING", None), ("PENDING", None),
    ]  # fmt: skip
    # A suppressed identity stays suppressed; it is never reconsidered.
    assert (
        await raise_alert(container, user, entry, game, trigger=trigger("900@t2"), now=after) == 0
    )


async def test_per_user_daily_limit(client: httpx.AsyncClient, auth, container: Container) -> None:  # type: ignore[no-untyped-def]
    container.settings.alert_max_per_user_per_day = 2
    container.settings.alert_cooldown_hours = 0
    user, entry, game = await setup_entry(container)
    for i in range(4):
        await raise_alert(container, user, entry, game, trigger=trigger(f"{i}@t"))
    states = [(r.state, r.error_code) for r in await outbox_rows(container)]
    assert states.count(("PENDING", None)) == 2
    assert states.count(("SUPPRESSED", "USER_DAILY_LIMIT")) == 2


async def test_quiet_hours_defer_delivery_instead_of_dropping(
    client: httpx.AsyncClient,
    auth,
    container: Container,  # type: ignore[no-untyped-def]
) -> None:
    user, entry, game = await setup_entry(container)
    async with container.db.session() as session:
        await client.get("/api/v1/notification-preferences", headers=auth)
        pref = (
            await session.scalars(
                select(NotificationPreference).where(NotificationPreference.channel == "EMAIL")
            )
        ).one()
        pref.quiet_hours_start, pref.quiet_hours_end, pref.timezone = time(22), time(7), "UTC"
        await session.commit()
    email_provider(container).sent.clear()
    night = datetime(2026, 5, 4, 23, 30, tzinfo=UTC)
    await raise_alert(container, user, entry, game, now=night)
    (row,) = await outbox_rows(container)
    assert row.state == "PENDING" and row.next_attempt_at == datetime(2026, 5, 5, 7, 0, tzinfo=UTC)
    assert await dispatch(container, now=night) == {}
    assert await dispatch(container, now=datetime(2026, 5, 5, 7, 1, tzinfo=UTC)) == {"SENT": 1}
    assert len(email_provider(container).sent) == 1


async def test_digest_mode_batches_alerts_into_one_message(
    client: httpx.AsyncClient,
    auth,
    container: Container,  # type: ignore[no-untyped-def]
) -> None:
    container.settings.alert_cooldown_hours = 0
    await client.patch(
        "/api/v1/notification-preferences",
        json={"preferences": [{"channel": "EMAIL", "delivery_mode": "DIGEST"}]}, headers=auth,
    )  # fmt: skip
    user, entry, game = await setup_entry(container)
    email_provider(container).sent.clear()
    for i in range(3):
        await raise_alert(container, user, entry, game, trigger=trigger(f"{i}@t"))
    assert await dispatch(container) == {}  # immediate dispatch ignores digest rows
    async with container.db.session() as session:
        dispatcher = OutboxDispatcher(session, container)
        wrong_hour = datetime(2026, 5, 4, 3, 0, tzinfo=UTC)
        assert await dispatcher.send_digests(now=utcnow().replace(hour=wrong_hour.hour)) in (0, 1)
        await dispatcher.send_digests(force=True)
    sent = email_provider(container).sent
    assert len(sent) == 1 and "3 alert(s)" in sent[0].title
    assert sent[0].body.count("•") == 3
    assert {r.state for r in await outbox_rows(container)} == {"SENT"}


# --- outbox: retries and terminal failures ---------------------------------


async def test_transient_failure_retries_with_backoff_and_sends_exactly_once(
    client: httpx.AsyncClient,
    auth,
    container: Container,  # type: ignore[no-untyped-def]
) -> None:
    user, entry, game = await setup_entry(container)
    provider = email_provider(container)
    provider.sent.clear()
    provider.script = [
        DeliveryResult.retry("EMAIL_HTTP_503"),
        DeliveryResult.retry("EMAIL_HTTP_503"),
    ]
    now = utcnow()
    await raise_alert(container, user, entry, game, now=now)

    assert await dispatch(container, now=now) == {"PENDING": 1}
    (row,) = await outbox_rows(container)
    assert row.error_code == "EMAIL_HTTP_503" and row.attempt_count == 1
    assert timedelta(seconds=15) <= row.next_attempt_at - now <= timedelta(seconds=30)
    assert await dispatch(container, now=now + timedelta(seconds=5)) == {}  # not due yet

    assert await dispatch(container, now=now + timedelta(minutes=1)) == {"PENDING": 1}
    assert await dispatch(container, now=now + timedelta(minutes=10)) == {"SENT": 1}
    assert await dispatch(container, now=now + timedelta(hours=2)) == {}
    assert len(provider.sent) == 1  # the user got exactly one message
    async with container.db.session() as session:
        deliveries = list(
            await session.scalars(
                select(NotificationDelivery).order_by(NotificationDelivery.attempt_number)
            )
        )
    assert [(d.attempt_number, d.success, d.transient) for d in deliveries] == [
        (1, False, True), (2, False, True), (3, True, False),
    ]  # fmt: skip


async def test_terminal_failure_is_not_retried_and_attempts_are_capped(
    client: httpx.AsyncClient,
    auth,
    container: Container,  # type: ignore[no-untyped-def]
) -> None:
    container.settings.alert_cooldown_hours = 0
    container.settings.outbox_max_attempts = 2
    user, entry, game = await setup_entry(container)
    provider = email_provider(container)
    provider.script = [DeliveryResult.fail("EMAIL_HTTP_422")]
    now = utcnow()
    await raise_alert(container, user, entry, game, trigger=trigger("a@t"), now=now)
    assert await dispatch(container, now=now) == {"FAILED": 1}
    assert await dispatch(container, now=now + timedelta(days=1)) == {}

    provider.script = [DeliveryResult.retry("EMAIL_NETWORK_ERROR")] * 5
    await raise_alert(container, user, entry, game, trigger=trigger("b@t"), now=now)
    assert await dispatch(container, now=now) == {"PENDING": 1}
    assert await dispatch(container, now=now + timedelta(hours=2)) == {"FAILED": 1}
    rows = await outbox_rows(container)
    assert [(r.state, r.error_code, r.attempt_count) for r in rows] == [
        ("FAILED", "EMAIL_HTTP_422", 1), ("FAILED", "EMAIL_NETWORK_ERROR", 2),
    ]  # fmt: skip


async def test_concurrent_workers_cannot_send_the_same_row_twice(
    client: httpx.AsyncClient,
    auth,
    container: Container,  # type: ignore[no-untyped-def]
) -> None:
    user, entry, game = await setup_entry(container)
    email_provider(container).sent.clear()
    await raise_alert(container, user, entry, game)
    (row,) = await outbox_rows(container)
    async with container.db.session() as one, container.db.session() as two:
        first = await OutboxDispatcher(one, container).dispatch_one(row.id)
        second = await OutboxDispatcher(two, container).dispatch_one(row.id)
    assert (first, second) == ("SENT", None)
    assert len(email_provider(container).sent) == 1


async def test_stale_sending_rows_are_released(
    client: httpx.AsyncClient, auth, container: Container
) -> None:  # type: ignore[no-untyped-def]
    user, entry, game = await setup_entry(container)
    await raise_alert(container, user, entry, game)
    now = utcnow()
    async with container.db.session() as session:
        row = (await session.scalars(select(NotificationOutbox))).one()
        row.state, row.locked_at = "SENDING", now - timedelta(hours=1)  # a crashed worker
        await session.commit()
    assert await dispatch(container, now=now) == {"SENT": 1}


async def test_web_push_removes_gone_subscriptions_and_keeps_working_ones(
    client: httpx.AsyncClient,
    auth,
    container: Container,  # type: ignore[no-untyped-def]
) -> None:
    await client.post("/api/v1/push-subscriptions", json=PUSH_BODY, headers=auth)
    second = {**PUSH_BODY, "endpoint": "https://updates.push.services.mozilla.com/wpush/v2/tok-2"}
    await client.post("/api/v1/push-subscriptions", json=second, headers=auth)
    user, entry, game = await setup_entry(container)
    push = push_provider(container)
    push.script = [
        DeliveryResult.fail("PUSH_GONE_410", destination_gone=True),
        DeliveryResult.ok("m1"),
    ]
    await raise_alert(container, user, entry, game, channels=["WEB_PUSH"])
    assert await dispatch(container) == {"SENT": 1}

    assert len(push.attempts) == 2 and len(push.sent) == 1
    target = push.attempts[0].push
    assert isinstance(target, PushTarget) and target.p256dh == PUSH_BODY["keys"]["p256dh"]  # type: ignore[index]
    async with container.db.session() as session:
        subs = list(await session.scalars(select(PushSubscription)))
        assert sum(1 for s in subs if s.revoked_at is not None) == 1  # 410 -> removed
        assert sum(1 for s in subs if s.last_success_at is not None) == 1
        assert await session.scalar(select(func.count()).select_from(NotificationDelivery)) == 2
    assert len((await client.get("/api/v1/push-subscriptions", headers=auth)).json()) == 1


async def test_channel_without_destination_is_skipped_not_failed(
    client: httpx.AsyncClient,
    auth,
    container: Container,  # type: ignore[no-untyped-def]
) -> None:
    user, entry, game = await setup_entry(container)
    # No push subscription and SMS disabled: only EMAIL produces an outbox row.
    created = await raise_alert(container, user, entry, game, channels=["WEB_PUSH", "EMAIL", "SMS"])
    assert created == 1
    assert [r.channel for r in await outbox_rows(container)] == ["EMAIL"]
    async with container.db.session() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationEvent)) == 1


async def test_unverified_email_blocks_alerts_when_required(
    client: httpx.AsyncClient,
    auth,
    container: Container,  # type: ignore[no-untyped-def]
) -> None:
    container.settings.require_verified_email_for_alerts = True
    user, entry, game = await setup_entry(container)
    assert await raise_alert(container, user, entry, game) == 0


# --- provider adapters ------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "success", "transient", "gone"),
    [(201, True, False, False), (404, False, False, True), (410, False, False, True),
     (429, False, True, False), (500, False, True, False), (503, False, True, False),
     (400, False, False, False), (413, False, False, False), (None, False, True, False)],
)  # fmt: skip
def test_push_status_classification(status, success, transient, gone) -> None:  # type: ignore[no-untyped-def]
    result = classify_push_status(status)
    assert (result.success, result.transient, result.destination_gone) == (success, transient, gone)


def test_push_payload_is_small_json() -> None:
    message = NotificationMessage(
        channel=Channel.WEB_PUSH, title="T" * 500, body="B" * 9000, url="https://example.invalid/g"
    )
    payload = build_payload(message)
    assert len(payload.encode()) <= 3000
    assert '"url": "https://example.invalid/g"' in payload


async def test_web_push_provider_maps_library_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import pywebpush

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.headers = {"Location": "https://push.example/m/abc123"}

    calls: list[dict] = []

    def fake_webpush(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        status = statuses.pop(0)
        if status >= 400:
            raise pywebpush.WebPushException("push failed", response=FakeResponse(status))
        return FakeResponse(status)

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    provider = WebPushProvider("private-key", "mailto:ops@example.com")
    message = NotificationMessage(
        channel=Channel.WEB_PUSH, title="Hi", body="There",
        push=PushTarget("https://push.example/send/tok", "p256", "auth"),
    )  # fmt: skip
    statuses = [201, 410, 503]
    ok, gone, retry = [await provider.send(message) for _ in range(3)]
    assert ok.success and ok.provider_message_id == "abc123"
    assert gone.destination_gone and not gone.transient
    assert retry.transient and retry.error_code == "PUSH_HTTP_503"
    assert calls[0]["vapid_claims"] == {"sub": "mailto:ops@example.com"}
    assert calls[0]["subscription_info"]["keys"] == {"p256dh": "p256", "auth": "auth"}
    assert not (await provider.send(NotificationMessage(Channel.WEB_PUSH, "a", "b"))).success


@respx.mock
async def test_resend_success_failure_and_idempotency_header() -> None:
    route = respx.post("https://api.resend.com/emails").mock(
        side_effect=[
            httpx.Response(200, json={"id": "re_123"}),
            httpx.Response(429),
            httpx.Response(422, json={"message": "invalid from"}),
            httpx.ConnectError("down"),
        ]
    )
    provider = ResendEmailProvider("re_secret", "Steam4Caster <alerts@example.com>")
    message = NotificationMessage(
        channel=Channel.EMAIL, title="Subject", body="Body <b>", url="https://example.invalid/?a=1&b=2",
        email="to@example.com", idempotency_key="idem-1",
    )  # fmt: skip
    ok, limited, invalid, network = [await provider.send(message) for _ in range(4)]
    await provider.aclose()
    assert ok.success and ok.provider_message_id == "re_123"
    assert limited.transient and limited.error_code == "EMAIL_HTTP_429"
    assert not invalid.success and not invalid.transient and invalid.error_code == "EMAIL_HTTP_422"
    assert network.transient and network.error_code == "EMAIL_NETWORK_ERROR"
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer re_secret"
    assert request.headers["Idempotency-Key"] == "idem-1"
    import json

    sent = json.loads(request.content)
    assert sent["to"] == ["to@example.com"] and sent["subject"] == "Subject"
    assert "Body &lt;b&gt;" in sent["html"] and "a=1&amp;b=2" in sent["html"]  # escaped
    assert "IsThereAnyDeal" in sent["text"]


async def test_smtp_provider_success_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[object] = []
    behaviour: list[Exception | None] = [
        None,
        smtplib.SMTPRecipientsRefused({"to@example.com": (550, b"no")}),
        smtplib.SMTPResponseException(451, "try later"),
        ConnectionRefusedError(),
    ]

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            assert (host, port) == ("mailpit", 1025)

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def send_message(self, message):  # type: ignore[no-untyped-def]
            outcome = behaviour.pop(0)
            if outcome is not None:
                raise outcome
            sent.append(message)

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    provider = SmtpEmailProvider("mailpit", 1025, "alerts@example.com")
    message = NotificationMessage(
        channel=Channel.EMAIL, title="Subject", body="Body", email="to@example.com"
    )
    ok, refused, later, down = [await provider.send(message) for _ in range(4)]
    assert ok.success and ok.provider_message_id and sent[0]["To"] == "to@example.com"  # type: ignore[index]
    assert refused.error_code == "EMAIL_RECIPIENT_REFUSED" and not refused.transient
    assert later.transient and later.error_code == "EMAIL_SMTP_451"
    assert down.transient and down.error_code == "EMAIL_NETWORK_ERROR"


async def test_sms_is_disabled_by_default_and_never_sends(container: Container) -> None:
    provider = container.notifiers[Channel.SMS]
    assert isinstance(provider, DisabledSmsProvider)
    result = await provider.send(
        NotificationMessage(channel=Channel.SMS, title="t", body="b", phone="+14155550123")
    )
    assert not result.success and result.error_code == "SMS_DISABLED" and not result.transient


@respx.mock
async def test_twilio_adapter_when_enabled() -> None:
    route = respx.post("https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json").mock(
        side_effect=[
            httpx.Response(201, json={"sid": "SM1"}),
            httpx.Response(400),
            httpx.Response(503),
        ]
    )
    provider = TwilioSmsProvider("AC123", "token", "+15005550006")
    message = NotificationMessage(
        channel=Channel.SMS, title="Sale", body="Now 50% off", phone="+14155550123"
    )
    ok, bad, retry = [await provider.send(message) for _ in range(3)]
    await provider.aclose()
    assert ok.provider_message_id == "SM1"
    assert not bad.transient and retry.transient
    body = route.calls[0].request.content.decode()
    assert "To=%2B14155550123" in body and "STOP" in body


def test_phone_numbers_are_validated_per_country() -> None:
    assert normalize_e164("+14155550123") == "+14155550123"
    assert normalize_e164("98765 43210", "IN") == "+919876543210"
    for bad in ("12345", "not a number", "+1415555"):
        with pytest.raises(ValueError):
            normalize_e164(bad, "US")
