from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.api.deps import ContainerDep

router = APIRouter(prefix="/health", tags=["system"])


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"


class Readiness(BaseModel):
    status: Literal["ready", "degraded", "unavailable"]
    components: dict[str, str]


@router.get("/live", response_model=Liveness, summary="Process liveness")
async def live() -> Liveness:
    return Liveness()


@router.get(
    "/ready",
    response_model=Readiness,
    summary="Readiness: database and required configuration",
    description="Returns 503 only when the database or required configuration is "
    "unavailable. A Redis outage is reported as `degraded` and does not fail readiness.",
    responses={503: {"model": Readiness}},
)
async def ready(container: ContainerDep, response: Response) -> Readiness:
    settings = container.settings
    database_ok = await container.db.ping()
    config_ok = not settings.startup_problems()
    cache_ok = await container.kv.ping()
    components = {
        "database": "ok" if database_ok else "unavailable",
        "configuration": "ok" if config_ok else "invalid",
        "cache": "ok" if cache_ok else "degraded",
        "price_provider": container.price_provider.name,
        "web_push": "configured" if settings.web_push_configured else "disabled",
        "email": settings.email_provider,
        "sms": "configured" if settings.sms_configured else "disabled",
    }
    if not (database_ok and config_ok):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Readiness(status="unavailable", components=components)
    return Readiness(status="ready" if cache_ok else "degraded", components=components)
