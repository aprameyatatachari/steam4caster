"""Single-process mode: the API runs the background jobs itself (no Celery worker)."""

from __future__ import annotations

import httpx

from app.core.container import Container
from app.core.timeutil import utcnow
from app.models.enums import Channel
from app.tasks import jobs
from app.tasks.inline import InlineRunner
from tests.conftest import email_provider, push_provider, register
from tests.helpers import ScriptedProvider, history_point, price_points, regular_sales, to_history
from tests.test_notifications import PUSH_BODY


async def test_alert_fires_for_a_price_already_below_target_without_a_worker(
    client: httpx.AsyncClient, container: Container
) -> None:
    """Adding a game that is already under the target must alert once, promptly."""
    provider = ScriptedProvider()
    # On sale right now at 50% off (1000), after a regular history.
    provider.series["US"] = [
        *to_history(price_points(utcnow(), sales=regular_sales(5, 60, 40))),
        history_point(utcnow(), cut=50),
    ]
    container.price_provider = provider
    runner = InlineRunner(container)
    container.tasks = runner

    auth = await register(client, "lite@example.com")
    await client.post("/api/v1/push-subscriptions", json=PUSH_BODY, headers=auth)
    game = (
        await client.get("/api/v1/games/search", params={"q": "scripted"}, headers=auth)
    ).json()[0]
    email_provider(container).sent.clear()
    created = await client.post(
        "/api/v1/watchlist",
        json={"game_id": game["id"], "target_price_minor": 1200, "channels": ["WEB_PUSH"]},
        headers=auth,
    )
    assert created.status_code == 201, created.text

    # The API queued the work on its own runner; let the runner process it.
    runner.start()
    try:
        await runner.drain()
    finally:
        await runner.stop()

    sent = push_provider(container).sent
    assert len(sent) == 1 and "target price" in sent[0].title
    assert "USD 10.00" in sent[0].body and "USD 12.00" in sent[0].body
    items = (await client.get("/api/v1/notifications", headers=auth)).json()["items"]
    assert [(n["event_type"], n["state"]) for n in items] == [("TARGET_PRICE", "SENT")]

    # Evaluating again (the scheduled pass) must not repeat the alert.
    runner.enqueue(jobs.PROCESS_SERIES, game["id"], "US")
    runner.start()
    try:
        await runner.drain()
    finally:
        await runner.stop()
    assert len(push_provider(container).sent) == 1


async def test_runner_dedupes_queued_work_and_survives_a_failing_job(container: Container) -> None:
    runner = InlineRunner(container)
    missing = "00000000-0000-0000-0000-000000000000"
    runner.enqueue(jobs.PROCESS_SERIES, missing, "US")
    runner.enqueue(jobs.PROCESS_SERIES, missing, "US")  # identical work is queued once
    runner.enqueue("no.such.task", 1)
    assert runner._queue.qsize() == 1
    runner.start()
    try:
        await runner.drain()  # the job fails (unknown game) but the worker keeps going
        runner.enqueue(jobs.PROCESS_SERIES, missing, "US")
        await runner.drain()
    finally:
        await runner.stop()
    assert not runner._pending


async def test_failed_test_attempts_do_not_use_up_the_hourly_allowance(
    client: httpx.AsyncClient, container: Container
) -> None:
    del container.notifiers[Channel.EMAIL]  # like single-process mode: push is the only channel
    auth = await register(client, "tester@example.com")
    for _ in range(5):
        nowhere = await client.post("/api/v1/notifications/test", headers=auth)
        assert nowhere.status_code == 422
        assert "browser push" in nowhere.json()["error"]["message"]

    await client.post("/api/v1/push-subscriptions", json=PUSH_BODY, headers=auth)
    statuses = [
        (await client.post("/api/v1/notifications/test", headers=auth)).status_code
        for _ in range(3)
    ]
    assert statuses == [200, 200, 429]  # the limit in test settings is 2 per hour
    assert len(push_provider(container).sent) == 2
