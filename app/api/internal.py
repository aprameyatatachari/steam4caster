"""Scheduler entry points for serverless hosting (``JOB_QUEUE=kv``).

A Cloudflare Worker cron calls these with ``Authorization: Bearer <CRON_SECRET>``.
They are not part of the public API and are hidden from the OpenAPI document. With no
``CRON_SECRET`` configured they do not exist (404).
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.deps import ContainerDep
from app.core.errors import AuthenticationError, NotFoundError
from app.tasks import queue
from app.tasks.schedule import claim_due, deadline_from_now, runner

router = APIRouter(prefix="/internal/jobs", include_in_schema=False)


async def require_scheduler(request: Request, container: ContainerDep) -> None:
    secret = container.settings.cron_secret
    if secret is None:
        raise NotFoundError("Not found.")
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), secret.get_secret_value().encode()):
        raise AuthenticationError("Invalid scheduler credentials.")


Scheduler = Depends(require_scheduler)


@router.post("/tick", dependencies=[Scheduler])
async def tick(container: ContainerDep) -> dict[str, Any]:
    """Claim the periodic jobs that are due. The caller runs each with ``/run/{name}``."""
    return {"due": await claim_due(container)}


@router.post("/run/{name}", dependencies=[Scheduler])
async def run(name: str, container: ContainerDep) -> dict[str, Any]:
    job = runner(container, name, deadline_from_now(container))
    if job is None:
        raise NotFoundError(f"No scheduled job named {name!r}.")
    return {"job": name, "result": await job()}


@router.post("/drain", dependencies=[Scheduler])
async def drain(container: ContainerDep) -> dict[str, Any]:
    """Run queued per-game work until the time budget is spent. Call again while
    ``remaining`` is above zero."""
    return await queue.drain(container, deadline_from_now(container))
