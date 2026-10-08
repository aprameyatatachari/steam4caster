"""FastAPI application factory."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.container import Container, build_container
from app.core.errors import ErrorEnvelope, register_error_handlers
from app.core.logging import configure_logging, correlation_id_var, get_logger
from app.core.metrics import HTTP_LATENCY, HTTP_REQUESTS
from app.schemas.common import ESTIMATE_NOTICE

logger = get_logger(__name__)

API_PREFIX = "/api/v1"

DESCRIPTION = f"""
Backend API for **Steam4Caster**: probabilistic Steam discount forecasts, explainable
BUY / WAIT / NEUTRAL recommendations, watchlists and alerts.

* All public routes are versioned under `/api/v1`.
* Authenticate with `Authorization: Bearer <access token>`.
* Money is always integer minor units plus an ISO 4217 currency, per region. Prices are
  never converted between currencies.
* Errors use one envelope: `{{"error": {{"code", "message", "correlation_id", "details"}}}}`.
* {ESTIMATE_NOTICE}

Price data is provided by the IsThereAnyDeal API. Steam4Caster is not affiliated with
IsThereAnyDeal or Valve.
"""

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (401, 404, 409, 422, 429, 503)
}


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or (container.settings if container else get_settings())
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = container is None
        app.state.container = container or build_container(settings)
        runner = None
        if settings.inline_jobs:
            from app.tasks.inline import InlineRunner

            runner = InlineRunner(app.state.container)
            app.state.container.tasks = runner
            runner.start()
        logger.info("application started", extra={"env": settings.app_env})
        try:
            yield
        finally:
            # Graceful shutdown: in-flight requests have finished by the time this runs.
            if runner is not None:
                await runner.stop()
            if owned:
                await app.state.container.aclose()
            logger.info("application stopped")

    app = FastAPI(
        title=f"{settings.app_name} API",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        responses=_ERROR_RESPONSES,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    if container is not None:
        app.state.container = container

    if settings.cors_origins:
        # Explicit allowlist only. Credentials are never combined with a wildcard origin.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Retry-After"],
            max_age=600,
        )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        correlation_id = (
            incoming if 8 <= len(incoming) <= 64 and incoming.isascii() else str(uuid.uuid4())
        )
        token = correlation_id_var.set(correlation_id)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = correlation_id
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            # Routes under an included router report their path without its prefix.
            if route is not None and not request.url.path.startswith(path.split("{")[0]):
                path = f"{API_PREFIX}{path}"
            elapsed = time.perf_counter() - started
            HTTP_REQUESTS.labels(request.method, path, str(status_code)).inc()
            HTTP_LATENCY.labels(request.method, path).observe(elapsed)
            if not path.startswith(("/health", "/metrics")):
                logger.info(
                    "request",
                    extra={
                        "method": request.method,
                        "route": path,
                        "status": status_code,
                        "duration_ms": round(elapsed * 1000, 1),
                    },
                )
            correlation_id_var.reset(token)

    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router)

    if settings.metrics_enabled:

        @app.get("/metrics", include_in_schema=False)
        async def metrics() -> Response:
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn app.main:app_factory --factory``."""
    return create_app()
