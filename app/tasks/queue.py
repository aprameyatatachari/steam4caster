"""A Redis-backed job queue for serverless hosting.

On Vercel there is no long-running worker: functions only run while serving a request
and are cut off after a fixed time. Background work therefore goes into a Redis list,
and an external scheduler (a Cloudflare Worker cron) calls ``/internal/jobs`` to run
due jobs and drain the queue, each call stopping inside a time budget.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.container import Container
from app.core.kv import KeyValueStore
from app.core.logging import get_logger
from app.tasks import jobs

logger = get_logger(__name__)

QUEUE_KEY = "jobq:items"
PENDING_PREFIX = "jobq:pending:"
# A queued item is forgotten after this long, so a lost item can be queued again.
PENDING_TTL_SECONDS = 6 * 3600

QueuedJob = Callable[..., Awaitable[dict[str, Any]]]
QUEUEABLE: dict[str, QueuedJob] = {
    jobs.PROCESS_SERIES: jobs.process_series,
    jobs.INGEST_HISTORY: jobs.ingest_history,
}


class KVQueueDispatcher:
    """A ``TaskDispatcher`` that buffers work during a request and writes it to Redis
    when the request ends (``enqueue`` is synchronous; Redis calls are not)."""

    def __init__(self, kv: KeyValueStore) -> None:
        self._kv = kv
        self._buffer: list[tuple[str, tuple[Any, ...]]] = []

    def enqueue(self, name: str, *args: Any, countdown: float | None = None) -> None:
        if name not in QUEUEABLE:
            logger.warning("job cannot be queued", extra={"task": name})
            return
        self._buffer.append((name, args))

    async def flush(self) -> int:
        """Write buffered jobs to the queue, skipping any already waiting there."""
        written = 0
        while self._buffer:
            name, args = self._buffer.pop(0)
            payload = json.dumps({"name": name, "args": list(args)}, sort_keys=True)
            if await self._kv.set_nx(PENDING_PREFIX + payload, "1", PENDING_TTL_SECONDS):
                await self._kv.push(QUEUE_KEY, payload)
                written += 1
        return written


async def drain(container: Container, deadline: float) -> dict[str, Any]:
    """Run queued jobs until the queue is empty or ``deadline`` (monotonic) passes.
    A job is only started while time remains, so the call ends inside the limit."""
    processed = failed = 0
    while time.monotonic() < deadline:
        payload = await container.kv.pop(QUEUE_KEY)
        if payload is None:
            break
        await container.kv.delete(PENDING_PREFIX + payload)
        item = json.loads(payload)
        job = QUEUEABLE.get(item["name"])
        if job is None:
            continue
        try:
            await job(container, *item["args"])
            processed += 1
        except Exception:
            # Every job is idempotent and the schedules re-queue stale series, so a
            # failure is logged and dropped rather than retried in a tight loop.
            failed += 1
            logger.exception("queued job failed", extra={"task": item["name"]})
        flush = getattr(container.tasks, "flush", None)
        if flush is not None:
            await flush()
    return {
        "processed": processed,
        "failed": failed,
        "remaining": await container.kv.length(QUEUE_KEY),
    }
