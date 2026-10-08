"""Celery task wrappers. Logic lives in ``app.tasks.jobs``."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from celery import Task
from celery.signals import worker_process_init

from app.core.config import get_settings
from app.core.container import Container, build_container
from app.core.errors import UpstreamUnavailable
from app.core.logging import configure_logging, get_logger
from app.core.metrics import JOB_DURATION, JOB_RUNS
from app.providers.pricing.base import ProviderError, ProviderRateLimited
from app.tasks import jobs
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)

# Replaceable in tests so tasks run against a prepared container.
container_factory: Callable[[], Container] | None = None

RETRY_KWARGS: dict[str, Any] = {
    "max_retries": 5,
    "retry_backoff": 5,  # exponential: 5s, 10s, 20s, ...
    "retry_backoff_max": 900,
    "retry_jitter": True,
}


@worker_process_init.connect
def _configure_worker(**_: Any) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)


def run_job(
    task: Task, job: Callable[..., Awaitable[dict[str, Any]]], *args: Any
) -> dict[str, Any]:
    """Run an async job in a fresh event loop with its own container.

    Transient provider failures are retried with exponential backoff and jitter; a
    provider ``Retry-After`` is honoured. Anything else fails the task immediately.
    """
    name = task.name or job.__name__

    async def main() -> dict[str, Any]:
        container = (
            container_factory()
            if container_factory is not None
            else build_container(get_settings(), pooled=False)
        )
        try:
            return await job(container, *args)
        finally:
            await container.aclose()

    started = time.perf_counter()
    try:
        result = asyncio.run(main())
    except ProviderRateLimited as exc:
        JOB_RUNS.labels(name, "retry").inc()
        delay = (exc.retry_after or 30.0) + random.uniform(0, 5)
        raise task.retry(exc=exc, countdown=delay) from exc
    except (ProviderError, UpstreamUnavailable) as exc:
        # UpstreamUnavailable is the service-level form of a transient provider failure.
        if isinstance(exc, ProviderError) and not exc.transient:
            JOB_RUNS.labels(name, "failed").inc()
            raise
        JOB_RUNS.labels(name, "retry").inc()
        backoff = min(RETRY_KWARGS["retry_backoff_max"], 5 * 2**task.request.retries)
        raise task.retry(exc=exc, countdown=random.uniform(backoff / 2, backoff)) from exc
    except Exception:
        JOB_RUNS.labels(name, "failed").inc()
        logger.exception("job failed", extra={"job": name})
        raise
    JOB_RUNS.labels(name, "ok").inc()
    JOB_DURATION.labels(name).observe(time.perf_counter() - started)
    logger.info("job finished", extra={"job": name, "result": result})
    return result


@celery_app.task(bind=True, name=jobs.PROCESS_SERIES, **RETRY_KWARGS)
def process_series(self: Task, game_id: str, country: str) -> dict[str, Any]:
    return run_job(self, jobs.process_series, game_id, country)


@celery_app.task(bind=True, name=jobs.INGEST_HISTORY, **RETRY_KWARGS)
def ingest_history(self: Task, game_id: str, country: str, full: bool = False) -> dict[str, Any]:
    return run_job(self, jobs.ingest_history, game_id, country, full)


@celery_app.task(bind=True, name="prices.refresh_watched", **RETRY_KWARGS)
def refresh_watched_prices(self: Task) -> dict[str, Any]:
    return run_job(self, jobs.refresh_watched_prices)


@celery_app.task(bind=True, name="prices.backfill_pending", **RETRY_KWARGS)
def backfill_pending(self: Task) -> dict[str, Any]:
    return run_job(self, jobs.backfill_pending)


@celery_app.task(bind=True, name="forecasts.generate_daily", **RETRY_KWARGS)
def generate_daily_forecasts(self: Task) -> dict[str, Any]:
    return run_job(self, jobs.generate_daily_forecasts)


@celery_app.task(bind=True, name="forecasts.evaluate_outcomes", **RETRY_KWARGS)
def evaluate_outcomes(self: Task) -> dict[str, Any]:
    return run_job(self, jobs.evaluate_outcomes)


@celery_app.task(bind=True, name="catalog.reconcile_metadata", **RETRY_KWARGS)
def reconcile_metadata(self: Task) -> dict[str, Any]:
    return run_job(self, jobs.reconcile_metadata)


@celery_app.task(bind=True, name="notifications.dispatch_outbox", **RETRY_KWARGS)
def dispatch_outbox(self: Task) -> dict[str, Any]:
    return run_job(self, jobs.dispatch_outbox)


@celery_app.task(bind=True, name="notifications.send_digests", **RETRY_KWARGS)
def send_digests(self: Task) -> dict[str, Any]:
    return run_job(self, jobs.send_digests)


@celery_app.task(bind=True, name="models.train_if_due", **RETRY_KWARGS)
def train_if_due(self: Task, force: bool = False) -> dict[str, Any]:
    return run_job(self, jobs.train_if_due, force)
