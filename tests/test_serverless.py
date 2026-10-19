"""Serverless hosting: scheduler endpoints, the Redis job queue, proxy IPs and URLs."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from pydantic import SecretStr
from sqlalchemy import func, select

from app.core.container import Container
from app.models import Forecast
from app.tasks import queue
from app.tasks.queue import KVQueueDispatcher
from app.tasks.schedule import Periodic, is_due
from tests.conftest import make_settings, register

SECRET = "s" * 40
AUTH = {"Authorization": f"Bearer {SECRET}"}


def enable_scheduler(container: Container) -> None:
    container.settings.cron_secret = SecretStr(SECRET)


def test_host_database_urls_are_made_asyncpg_compatible(tmp_path: Path) -> None:
    neon = (
        "postgresql://user:pw@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )
    settings = make_settings(tmp_path, database_url=neon)
    assert settings.database_url == (
        "postgresql+asyncpg://user:pw@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb?ssl=require"
    )
    render = make_settings(tmp_path, database_url="postgres://u:p@host:5432/db")
    assert render.database_url == "postgresql+asyncpg://u:p@host:5432/db"
    sqlite = make_settings(tmp_path, database_url="sqlite+aiosqlite:///./x.db")
    assert sqlite.database_url == "sqlite+aiosqlite:///./x.db"


def test_queue_mode_requires_redis_and_a_long_secret(tmp_path: Path) -> None:
    problems = make_settings(tmp_path, job_queue="kv").startup_problems()
    assert any("KV_BACKEND=redis" in p for p in problems)
    assert any("CRON_SECRET" in p for p in problems)


async def test_scheduler_routes_are_hidden_without_a_secret_and_need_it_with_one(
    client: httpx.AsyncClient, container: Container
) -> None:
    assert (await client.post("/internal/jobs/tick", headers=AUTH)).status_code == 404
    enable_scheduler(container)
    assert (await client.post("/internal/jobs/tick")).status_code == 401
    wrong = {"Authorization": "Bearer " + "x" * 40}
    assert (await client.post("/internal/jobs/tick", headers=wrong)).status_code == 401
    schema = (await client.get("/openapi.json")).json()
    assert not any(path.startswith("/internal") for path in schema["paths"])


async def test_tick_claims_each_due_job_once(
    client: httpx.AsyncClient, container: Container
) -> None:
    enable_scheduler(container)
    first = (await client.post("/internal/jobs/tick", headers=AUTH)).json()["due"]
    assert {"dispatch_outbox", "refresh_watched_prices", "backfill_pending"} <= set(first)
    assert "train_if_due" not in first  # no model training on serverless hosts
    again = (await client.post("/internal/jobs/tick", headers=AUTH)).json()["due"]
    assert again == []


def test_daily_and_interval_schedules() -> None:
    daily = Periodic("x", daily_at=(5, 10))
    morning = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
    assert is_due(daily, None, morning)
    assert not is_due(daily, morning - timedelta(minutes=30), morning)  # ran today
    assert is_due(daily, morning - timedelta(days=1), morning)
    before_slot = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)
    assert not is_due(daily, before_slot - timedelta(hours=10), before_slot)  # ran yesterday
    hourly = Periodic("y", every=timedelta(hours=1))
    assert not is_due(hourly, morning - timedelta(minutes=20), morning)
    assert is_due(hourly, morning - timedelta(minutes=59, seconds=40), morning)


async def test_run_executes_a_scheduled_job_and_rejects_unknown_names(
    client: httpx.AsyncClient, container: Container
) -> None:
    enable_scheduler(container)
    ok = await client.post("/internal/jobs/run/dispatch_outbox", headers=AUTH)
    assert ok.status_code == 200, ok.text
    assert ok.json()["job"] == "dispatch_outbox"
    assert (await client.post("/internal/jobs/run/rm-rf", headers=AUTH)).status_code == 404
    assert (await client.post("/internal/jobs/run/train_if_due", headers=AUTH)).status_code == 404


async def test_work_queued_by_a_request_is_drained_by_the_scheduler(
    client: httpx.AsyncClient, container: Container
) -> None:
    enable_scheduler(container)
    container.tasks = KVQueueDispatcher(container.kv)
    auth = await register(client, "queue@example.com")
    game = (await client.get("/api/v1/games/search", params={"q": "Hollow"}, headers=auth)).json()[
        0
    ]
    created = await client.post("/api/v1/watchlist", json={"game_id": game["id"]}, headers=auth)
    assert created.status_code == 201, created.text
    # Adding to the watchlist queued the game's first refresh, once.
    assert await container.kv.length(queue.QUEUE_KEY) == 1

    drained = (await client.post("/internal/jobs/drain", headers=AUTH)).json()
    assert drained == {"processed": 1, "failed": 0, "remaining": 0}
    async with container.db.session() as session:
        assert await session.scalar(select(func.count()).select_from(Forecast)) >= 1


async def test_queue_skips_duplicates_and_drain_stops_at_its_deadline(
    container: Container,
) -> None:
    dispatcher = KVQueueDispatcher(container.kv)
    container.tasks = dispatcher
    dispatcher.enqueue("prices.process_series", "a", "US")
    dispatcher.enqueue("prices.process_series", "a", "US")
    dispatcher.enqueue("prices.process_series", "b", "US")
    dispatcher.enqueue("not.a.job")
    assert await dispatcher.flush() == 2
    dispatcher.enqueue("prices.process_series", "a", "US")
    assert await dispatcher.flush() == 0  # already waiting
    result = await queue.drain(container, deadline=time.monotonic() - 1)
    assert result == {"processed": 0, "failed": 0, "remaining": 2}


async def test_rate_limits_use_the_visitor_ip_behind_a_trusted_proxy(
    client: httpx.AsyncClient, container: Container
) -> None:
    container.settings.rate_limit_public_per_minute = 1
    container.settings.trusted_ip_header = "x-forwarded-for"
    one = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}
    two = {"x-forwarded-for": "198.51.100.9"}
    assert (await client.get("/api/v1/public/calibration", headers=one)).status_code == 200
    assert (await client.get("/api/v1/public/calibration", headers=one)).status_code == 429
    # A different visitor behind the same proxy has their own allowance.
    assert (await client.get("/api/v1/public/calibration", headers=two)).status_code == 200
