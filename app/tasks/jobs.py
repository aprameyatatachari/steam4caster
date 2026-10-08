"""Background job logic as plain async functions.

Celery tasks in ``app.tasks.tasks`` are thin wrappers around these, which keeps domain
logic out of task functions and lets tests call the jobs directly. Every job is
idempotent and safe under at-least-once execution.
"""

from __future__ import annotations

import random
import uuid
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select

from app.core.container import Container
from app.core.kv import distributed_lock
from app.core.logging import get_logger
from app.core.timeutil import utcnow
from app.forecasting.training.trainer import InsufficientTrainingData
from app.models import Game
from app.providers.pricing.base import ProviderError
from app.repositories import forecasts as forecasts_repo
from app.repositories import prices as prices_repo
from app.services.alerts import AlertService
from app.services.catalog import CatalogService
from app.services.dispatcher import OutboxDispatcher
from app.services.forecasts import ForecastService
from app.services.models import ModelService
from app.services.performance import PerformanceService
from app.services.prices import PriceService

logger = get_logger(__name__)

PROCESS_SERIES = "prices.process_series"
INGEST_HISTORY = "prices.ingest_history"


def _stagger(container: Container) -> float:
    return random.uniform(0, container.settings.schedule_stagger_seconds)


async def process_series(container: Container, game_id: str, country: str) -> dict[str, Any]:
    """Bring one game/region up to date: history, current price, forecast, alerts."""
    async with container.db.session() as session:
        catalog = CatalogService(session, container)
        game = await catalog.ensure_info(await catalog.get(uuid.UUID(game_id)))
        shop = await catalog.steam_shop()
        forecast = await ForecastService(session, container).get_or_generate(game, shop, country)
        alerts = await AlertService(session, container).evaluate_series(game, shop, country)
        return {"forecast_id": str(forecast.id), "alerts_created": alerts}


async def ingest_history(
    container: Container, game_id: str, country: str, full: bool = False
) -> dict[str, Any]:
    async with container.db.session() as session:
        catalog = CatalogService(session, container)
        game = await catalog.ensure_info(await catalog.get(uuid.UUID(game_id)))
        shop = await catalog.steam_shop()
        result = await PriceService(session, container).ingest_history(
            game, shop, country, full=full
        )
        return {
            "skipped": result.skipped,
            "inserted": result.inserted,
            "events": result.sale_events,
        }


async def refresh_watched_prices(container: Container) -> dict[str, Any]:
    """Refresh current prices for every watched game, one batched provider call per
    country, then fan out per-series follow-up work only where the price changed."""
    async with distributed_lock(container.kv, "job:refresh_watched_prices", ttl=900) as acquired:
        if not acquired:
            return {"skipped": True}
        async with container.db.session() as session:
            shop = await CatalogService(session, container).steam_shop()
            by_country: dict[str, set[uuid.UUID]] = defaultdict(set)
            for game_id, country in await forecasts_repo.watched_series(session):
                by_country[country].add(game_id)  # many users, one game/region refresh
            changed_total = 0
            errors = 0
            for country, game_ids in by_country.items():
                games = list(await session.scalars(select(Game).where(Game.id.in_(game_ids))))
                try:
                    changed = await PriceService(session, container).refresh_current(
                        games, shop, country
                    )
                except ProviderError as exc:
                    errors += 1
                    logger.warning(
                        "price refresh failed", extra={"country": country, "code": exc.code}
                    )
                    continue
                changed_total += len(changed)
                for game_id in changed:
                    container.tasks.enqueue(
                        PROCESS_SERIES, str(game_id), country, countdown=_stagger(container)
                    )
            return {
                "countries": len(by_country),
                "series": sum(len(v) for v in by_country.values()),
                "changed": changed_total,
                "errors": errors,
            }


async def backfill_pending(container: Container, limit: int = 200) -> dict[str, Any]:
    """Queue history backfills for watched series that have never completed one."""
    async with container.db.session() as session:
        shop = await CatalogService(session, container).steam_shop()
        queued = 0
        for game_id, country in await forecasts_repo.watched_series(session):
            watermark = await prices_repo.get_watermark(session, game_id, shop.id, country)
            if watermark is None or watermark.backfill_completed_at is None:
                container.tasks.enqueue(
                    PROCESS_SERIES, str(game_id), country, countdown=_stagger(container)
                )
                queued += 1
                if queued >= limit:
                    break
        return {"queued": queued}


async def generate_daily_forecasts(container: Container) -> dict[str, Any]:
    async with container.db.session() as session:
        series = await forecasts_repo.watched_series(session)
    for game_id, country in series:
        container.tasks.enqueue(
            PROCESS_SERIES, str(game_id), country, countdown=_stagger(container)
        )
    return {"queued": len(series)}


async def reconcile_metadata(container: Container, limit: int = 200) -> dict[str, Any]:
    """Refresh stale game metadata (stable data, so this runs daily or less often)."""
    cutoff = utcnow() - timedelta(days=container.settings.metadata_refresh_days)
    async with container.db.session() as session:
        catalog = CatalogService(session, container)
        await catalog.sync_shops()
        games = list(
            await session.scalars(
                select(Game)
                .where(
                    Game.itad_id.is_not(None),
                    or_(Game.info_fetched_at.is_(None), Game.info_fetched_at < cutoff),
                )
                .order_by(Game.info_fetched_at.asc().nulls_first())
                .limit(limit)
            )
        )
        refreshed = 0
        for game in games:
            before = game.info_fetched_at
            await catalog.ensure_info(game, force=True)
            refreshed += int(game.info_fetched_at != before)
        return {"candidates": len(games), "refreshed": refreshed}


async def evaluate_outcomes(container: Container) -> dict[str, Any]:
    async with container.db.session() as session:
        return {"evaluated": await PerformanceService(session, container).evaluate_matured()}


async def dispatch_outbox(container: Container) -> dict[str, Any]:
    async with container.db.session() as session:
        return await OutboxDispatcher(session, container).dispatch_pending()


async def send_digests(container: Container) -> dict[str, Any]:
    async with container.db.session() as session:
        return {"digests_sent": await OutboxDispatcher(session, container).send_digests()}


async def train_if_due(container: Container, force: bool = False) -> dict[str, Any]:
    """Train a candidate when enough new data has arrived. Never activates it."""
    settings = container.settings
    if not (settings.training_schedule_enabled or force):
        return {"skipped": "training schedule disabled"}
    async with distributed_lock(container.kv, "job:train", ttl=3600) as acquired:
        if not acquired:
            return {"skipped": "another training run holds the lock"}
        async with container.db.session() as session:
            service = ModelService(session, container)
            new_rows = await service.new_observations_since_last_training()
            if not force and new_rows < settings.training_min_new_observations:
                return {"skipped": "not enough new observations", "new_observations": new_rows}
            shop = await CatalogService(session, container).steam_shop()
            try:
                version = await service.train(shop)
            except InsufficientTrainingData as exc:
                return {"skipped": str(exc)}
            return {
                "version": version.version,
                "gates_passed": bool((version.metrics.get("gates") or {}).get("passed")),
            }
