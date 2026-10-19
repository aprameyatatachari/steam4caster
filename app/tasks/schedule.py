"""When each periodic job is due, for hosts without Celery Beat.

Mirrors the Beat schedule in ``app.tasks.celery_app``. The external scheduler calls
``/internal/jobs/tick`` every few minutes; the tick claims whichever jobs are due (the
claim is stored in Redis, so overlapping ticks never run a job twice) and the scheduler
then runs each one with its own request.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.container import Container
from app.core.timeutil import utcnow
from app.tasks import jobs

LAST_RUN_PREFIX = "jobsched:last:"
CLAIM_TTL_SECONDS = 120

JobCall = Callable[[], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Periodic:
    name: str
    every: timedelta | None = None
    # Daily jobs: the UTC time of day after which the day's run is due.
    daily_at: tuple[int, int] | None = None


def schedule(container: Container) -> list[Periodic]:
    settings = container.settings
    refresh = timedelta(minutes=settings.schedule_price_refresh_minutes)
    plan = [
        Periodic("dispatch_outbox", every=timedelta(minutes=1)),
        Periodic("refresh_watched_prices", every=refresh),
        Periodic("backfill_pending", every=timedelta(hours=1)),
        Periodic("send_digests", every=timedelta(hours=1)),
        Periodic("warm_showcase", every=timedelta(hours=1)),
        Periodic("reconcile_metadata", daily_at=(3, 30)),
        Periodic("generate_daily_forecasts", daily_at=(5, 10)),
        Periodic("evaluate_outcomes", daily_at=(6, 0)),
    ]  # fmt: skip
    # Training needs minutes and a persistent disk for the model file; neither exists
    # on serverless hosts, so it only runs where the schedule is explicitly enabled.
    if settings.training_schedule_enabled:
        plan.append(Periodic("train_if_due", every=timedelta(days=7)))
    return plan


def is_due(job: Periodic, last: datetime | None, now: datetime) -> bool:
    if job.every is not None:
        # A little slack so a tick that lands a few seconds early still counts.
        return last is None or now - last >= job.every - timedelta(seconds=30)
    assert job.daily_at is not None
    slot = now.replace(hour=job.daily_at[0], minute=job.daily_at[1], second=0, microsecond=0)
    if now < slot:
        slot -= timedelta(days=1)
    return last is None or last < slot


async def claim_due(container: Container, now: datetime | None = None) -> list[str]:
    """Mark due jobs as started and return their names."""
    now = now or utcnow()
    claimed: list[str] = []
    for job in schedule(container):
        raw = await container.kv.get(LAST_RUN_PREFIX + job.name)
        last = datetime.fromisoformat(raw) if raw else None
        if not is_due(job, last, now):
            continue
        # Two overlapping ticks: only one wins the claim.
        if not await container.kv.set_nx(f"jobsched:claim:{job.name}", "1", CLAIM_TTL_SECONDS):
            continue
        await container.kv.set(LAST_RUN_PREFIX + job.name, now.isoformat())
        claimed.append(job.name)
    return claimed


def runner(container: Container, name: str, deadline: float) -> JobCall | None:
    """The coroutine factory for a scheduled job, bound to a time budget."""
    known: dict[str, JobCall] = {
        "dispatch_outbox": lambda: jobs.dispatch_outbox(container),
        "refresh_watched_prices": lambda: jobs.refresh_watched_prices(container),
        "backfill_pending": lambda: jobs.backfill_pending(container),
        "send_digests": lambda: jobs.send_digests(container),
        "warm_showcase": lambda: jobs.warm_showcase(container),
        "reconcile_metadata": lambda: jobs.reconcile_metadata(container, deadline=deadline),
        "generate_daily_forecasts": lambda: jobs.generate_daily_forecasts(container),
        "evaluate_outcomes": lambda: jobs.evaluate_outcomes(container),
        "train_if_due": lambda: jobs.train_if_due(container),
    }  # fmt: skip
    if name not in {job.name for job in schedule(container)}:
        return None
    return known.get(name)


def deadline_from_now(container: Container) -> float:
    return time.monotonic() + container.settings.job_time_budget_seconds
