"""Celery task behaviour: retries on transient failures and safety under duplicate
(at-least-once) execution. Tasks run eagerly against a file-backed SQLite database."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.container import Container, build_container
from app.core.kv import MemoryKV
from app.core.timeutil import utcnow
from app.db.base import Base
from app.models import (
    Forecast,
    NotificationOutbox,
    PriceObservation,
    SaleEvent,
    User,
    WatchlistEntry,
)
from app.models.enums import Channel
from app.providers.notifications.base import FakeNotificationProvider
from app.providers.pricing.base import ProviderAuthError, ProviderRateLimited, ProviderUnavailable
from app.services.catalog import CatalogService
from app.tasks import tasks
from app.tasks.celery_app import celery_app
from tests.conftest import make_settings
from tests.helpers import ScriptedProvider, history_point, price_points, regular_sales, to_history


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.settings = make_settings(
            tmp_path, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'tasks.db').as_posix()}"
        )
        self.provider = ScriptedProvider()
        self.provider.series["US"] = to_history(
            price_points(utcnow(), sales=regular_sales(6, 60, 30))
        )
        self.email = FakeNotificationProvider(Channel.EMAIL)
        self.game_id = ""

    def container(self) -> Container:
        built = build_container(self.settings)
        built.price_provider = self.provider
        built.kv = MemoryKV()
        built.notifiers[Channel.EMAIL] = self.email
        return built

    def run(self, coro_factory):  # type: ignore[no-untyped-def]
        async def main():  # type: ignore[no-untyped-def]
            container = self.container()
            try:
                return await coro_factory(container)
            finally:
                await container.aclose()

        return asyncio.run(main())

    def count(self, model: type) -> int:
        async def query(container: Container) -> int:
            async with container.db.session() as session:
                return int(await session.scalar(select(func.count()).select_from(model)) or 0)

        return self.run(query)

    def setup(self) -> None:
        async def create(container: Container) -> str:
            async with container.db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with container.db.session() as session:
                catalog = CatalogService(session, container)
                await catalog.steam_shop()
                game = await catalog.ensure_info((await catalog.search("script"))[0])
                user = User(email="tasks@example.com", default_country="US", default_currency="USD")
                session.add(user)
                await session.flush()
                session.add(
                    WatchlistEntry(
                        user_id=user.id, game_id=game.id, country="US", currency="USD",
                        min_discount_pct=40, channels=["EMAIL"],
                    )
                )  # fmt: skip
                await session.commit()
                return str(game.id)

        self.game_id = self.run(create)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    instance = Harness(tmp_path)
    instance.setup()
    monkeypatch.setattr(tasks, "container_factory", instance.container)
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", False)
    yield instance


def test_duplicate_execution_creates_no_duplicate_rows(harness: Harness) -> None:
    first = tasks.process_series.apply(args=[harness.game_id, "US"])
    assert first.successful(), first.traceback
    counts = {m: harness.count(m) for m in (PriceObservation, SaleEvent, Forecast)}
    assert counts == {PriceObservation: 13, SaleEvent: 6, Forecast: 1}

    # The same message delivered again (at-least-once) must be a no-op.
    for _ in range(3):
        replay = tasks.process_series.apply(args=[harness.game_id, "US"])
        assert replay.successful() and replay.result["forecast_id"] == first.result["forecast_id"]
    assert {m: harness.count(m) for m in counts} == counts

    # A full history replay is also absorbed by the observation identity constraint.
    again = tasks.ingest_history.apply(args=[harness.game_id, "US", True])
    assert again.result["inserted"] == 0
    assert harness.count(PriceObservation) == 13


def test_duplicate_alert_evaluation_and_dispatch_send_once(harness: Harness) -> None:
    tasks.process_series.apply(args=[harness.game_id, "US"])
    harness.provider.series["US"].append(history_point(utcnow(), cut=50))
    refreshed = tasks.refresh_watched_prices.apply()
    assert refreshed.result["changed"] == 1

    results = [tasks.process_series.apply(args=[harness.game_id, "US"]).result for _ in range(3)]
    # The discount alert plus the WAIT -> BUY transition, each exactly once.
    assert [r["alerts_created"] for r in results] == [2, 0, 0]
    assert harness.count(NotificationOutbox) == 2

    sent = [tasks.dispatch_outbox.apply().result for _ in range(3)]
    assert sent == [{"SENT": 2}, {}, {}]
    assert len(harness.email.sent) == 2
    assert any("50% off" in message.title for message in harness.email.sent)


def test_transient_provider_failure_is_retried_until_it_succeeds(harness: Harness) -> None:
    harness.provider.failures = [ProviderUnavailable("blip"), ProviderUnavailable("blip")]
    result = tasks.ingest_history.apply(args=[harness.game_id, "US", True])
    assert result.state in {"SUCCESS", "RETRY"}  # eager mode runs the retries inline
    assert harness.provider.calls["history"] == 3  # two failures, then success
    assert harness.count(PriceObservation) == 13  # and exactly one copy of the data


def test_rate_limit_is_retried_and_auth_errors_are_not(harness: Harness) -> None:
    harness.provider.failures = [ProviderRateLimited(retry_after=1)]
    tasks.ingest_history.apply(args=[harness.game_id, "US", True])
    assert harness.provider.calls["history"] == 2 and harness.count(PriceObservation) == 13

    harness.provider.failures = [ProviderAuthError("bad key")]
    failed = tasks.ingest_history.apply(args=[harness.game_id, "US", True])
    assert failed.state == "FAILURE" and isinstance(failed.result, ProviderAuthError)
    assert harness.provider.calls["history"] == 3  # no retry for a terminal error


def test_retries_are_bounded(harness: Harness) -> None:
    harness.provider.failures = [ProviderUnavailable("down") for _ in range(50)]
    tasks.ingest_history.apply(args=[harness.game_id, "US", True])
    assert harness.provider.calls["history"] == tasks.RETRY_KWARGS["max_retries"] + 1
    assert harness.count(PriceObservation) == 0


def test_schedule_and_task_registration() -> None:
    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert scheduled <= set(celery_app.tasks.keys())
    assert {"prices.refresh_watched", "forecasts.evaluate_outcomes", "notifications.dispatch_outbox",
            "models.train_if_due"} <= scheduled  # fmt: skip
    assert celery_app.conf.task_acks_late is True


def test_training_job_is_disabled_by_default(harness: Harness) -> None:
    assert tasks.train_if_due.apply().result == {"skipped": "training schedule disabled"}
    forced = tasks.train_if_due.apply(args=[True]).result
    assert "skipped" in forced  # one short series is not enough data to train on
