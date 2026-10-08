"""In-process job runner for single-process deployments (no Celery worker).

With ``INLINE_JOBS=true`` the API process itself does the background work: it keeps
watched games' prices fresh, generates forecasts, evaluates alerts and delivers
notifications. It runs the same idempotent jobs the Celery worker runs, one at a time,
so it is safe on SQLite. Use the real worker and scheduler for anything multi-process.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.container import Container
from app.core.logging import get_logger
from app.tasks import jobs

logger = get_logger(__name__)

Job = Callable[..., Awaitable[dict[str, Any]]]

OUTBOX_INTERVAL_SECONDS = 60
DIGEST_INTERVAL_SECONDS = 3600
OUTCOME_INTERVAL_SECONDS = 24 * 3600
STARTUP_DELAY_SECONDS = 5


class InlineRunner:
    """A ``TaskDispatcher`` that runs jobs on the API's own event loop."""

    def __init__(self, container: Container) -> None:
        self._container = container
        self._queue: asyncio.Queue[tuple[str, Job, tuple[Any, ...]]] = asyncio.Queue()
        self._pending: set[str] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self._jobs: dict[str, Job] = {
            jobs.PROCESS_SERIES: jobs.process_series,
            jobs.INGEST_HISTORY: jobs.ingest_history,
        }

    # --- TaskDispatcher -------------------------------------------------------

    def enqueue(self, name: str, *args: Any, countdown: float | None = None) -> None:
        job = self._jobs.get(name)
        if job is None:
            logger.warning("inline runner has no job for task", extra={"task": name})
            return
        key = f"{name}:{args}"
        if key in self._pending:  # the same work is already waiting
            return
        self._pending.add(key)
        self._queue.put_nowait((key, job, args))

    # --- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._work(), name="inline-jobs-worker"),
            asyncio.create_task(self._schedule(), name="inline-jobs-schedule"),
        ]
        logger.info("inline job runner started")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    async def drain(self) -> None:
        """Wait until every queued job has finished (used by tests)."""
        await self._queue.join()

    # --- internals ------------------------------------------------------------

    async def _run(self, label: str, job: Job, *args: Any) -> None:
        try:
            result = await job(self._container, *args)
            logger.info("inline job finished", extra={"job": label, "result": result})
        except asyncio.CancelledError:
            raise
        except Exception:
            # A failed job is retried by the next scheduled pass; never kill the loop.
            logger.exception("inline job failed", extra={"job": label})

    async def _work(self) -> None:
        while True:
            key, job, args = await self._queue.get()
            try:
                await self._run(job.__name__, job, *args)
                if job is jobs.process_series:
                    # Deliver anything that evaluation just raised without waiting a minute.
                    await self._run("dispatch_outbox", jobs.dispatch_outbox)
            finally:
                self._pending.discard(key)
                self._queue.task_done()

    async def _schedule(self) -> None:
        await asyncio.sleep(STARTUP_DELAY_SECONDS)
        refresh_every = self._container.settings.schedule_price_refresh_minutes * 60
        # First pass: bring every watched game up to date, then evaluate its alerts.
        await self._run("generate_daily_forecasts", jobs.generate_daily_forecasts)
        last = dict.fromkeys(("refresh", "digest", "outcomes"), time.monotonic())
        while True:
            await asyncio.sleep(OUTBOX_INTERVAL_SECONDS)
            now = time.monotonic()
            await self._run("dispatch_outbox", jobs.dispatch_outbox)
            if now - last["refresh"] >= refresh_every:
                last["refresh"] = now
                await self._run("refresh_watched_prices", jobs.refresh_watched_prices)
                await self._run("generate_daily_forecasts", jobs.generate_daily_forecasts)
            if now - last["digest"] >= DIGEST_INTERVAL_SECONDS:
                last["digest"] = now
                await self._run("send_digests", jobs.send_digests)
            if now - last["outcomes"] >= OUTCOME_INTERVAL_SECONDS:
                last["outcomes"] = now
                await self._run("evaluate_outcomes", jobs.evaluate_outcomes)
