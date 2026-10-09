"""Model lifecycle: dataset assembly, candidate training, activation and rollback."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.timeutil import utcnow
from app.forecasting.datasets.builder import SeriesData, build_dataset
from app.forecasting.inference.registry import save_artifact
from app.forecasting.training.trainer import evaluate_baseline, train_candidate
from app.forecasting.types import FEATURE_SCHEMA_VERSION, GameContext, PricePoint
from app.models import Game, ModelVersion, PriceObservation, Shop
from app.models.enums import ModelStatus
from app.services.performance import json_safe

logger = get_logger(__name__)

MODEL_NAME = "sale-forecaster-lgbm"


class ModelService:
    def __init__(self, session: AsyncSession, container: Container) -> None:
        self.session = session
        self.container = container
        self.settings = container.settings

    async def load_series(self, shop: Shop, country: str | None = None) -> list[SeriesData]:
        """All stored series for the shop. Each (game, country) is its own series and
        keeps its own currency; nothing is pooled across currencies."""
        stmt = (
            select(
                PriceObservation.game_id,
                PriceObservation.country,
                PriceObservation.currency,
                PriceObservation.observed_at,
                PriceObservation.price_minor,
                PriceObservation.regular_minor,
                PriceObservation.discount_pct,
            )
            .where(PriceObservation.shop_id == shop.id)
            .order_by(
                PriceObservation.game_id, PriceObservation.country, PriceObservation.observed_at
            )
        )
        if country:
            stmt = stmt.where(PriceObservation.country == country)
        grouped: dict[tuple[Any, str], list[tuple[str, PricePoint]]] = {}
        for row in (await self.session.execute(stmt)).all():
            grouped.setdefault((row[0], row[1]), []).append(
                (row[2], PricePoint(row[3], row[4], row[5], row[6]))
            )
        games = {g.id: g for g in await self.session.scalars(select(Game))}
        series: list[SeriesData] = []
        for (game_id, region), items in grouped.items():
            game = games.get(game_id)
            if game is None:
                continue
            currency = items[-1][0]
            series.append(
                SeriesData(
                    series_key=f"{game_id}:{region}",
                    game_key=game_id,
                    points=[p for c, p in items if c == currency],
                    context=GameContext(
                        release_date=game.release_date,
                        publisher=game.primary_publisher,
                        primary_tag=game.primary_tag,
                        type=game.type,
                    ),
                )
            )
        return series

    async def build_dataset(
        self, shop: Shop, *, as_of: datetime | None = None, country: str | None = None,
        step_days: int = 14,
    ) -> pd.DataFrame:  # fmt: skip
        series = await self.load_series(shop, country)
        return await asyncio.to_thread(build_dataset, series, as_of or utcnow(), step_days)

    async def evaluate_baseline(
        self, shop: Shop, *, as_of: datetime | None = None
    ) -> dict[str, Any]:
        frame = await self.build_dataset(shop, as_of=as_of)
        if frame.empty:
            return {"n_rows": 0}
        return json_safe(await asyncio.to_thread(evaluate_baseline, frame))

    async def train(self, shop: Shop, *, as_of: datetime | None = None) -> ModelVersion:
        """Train a CANDIDATE. Activation is a separate, explicit step."""
        as_of = as_of or utcnow()
        frame = await self.build_dataset(shop, as_of=as_of)
        result = await asyncio.to_thread(
            train_candidate, frame, min_rows=self.settings.training_min_rows
        )
        version = f"lgbm-{as_of:%Y%m%d%H%M%S}"
        path, checksum = await asyncio.to_thread(
            save_artifact, self.settings.model_artifact_path, version, result.payload
        )
        row = ModelVersion(
            name=MODEL_NAME,
            version=version,
            artifact_uri=path,
            artifact_checksum=checksum,
            training_cutoff=result.training_cutoff,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            hyperparameters=json_safe(result.hyperparameters),
            metrics=json_safe(result.metrics),
            status=ModelStatus.CANDIDATE.value,
        )
        self.session.add(row)
        await self.session.commit()
        logger.info(
            "trained candidate model",
            extra={"model_version": version, "promotion_ok": result.gates_passed},
        )
        return row

    async def get_version(self, version: str) -> ModelVersion:
        row = (
            await self.session.scalars(select(ModelVersion).where(ModelVersion.version == version))
        ).first()
        if row is None:
            raise NotFoundError(f"Model version {version!r} not found.")
        return row

    async def activate(self, version: str, *, force: bool = False) -> ModelVersion:
        """Make ``version`` the single ACTIVE model, retiring the previous one."""
        row = await self.get_version(version)
        gates = (row.metrics or {}).get("gates") or {}
        if not gates.get("passed") and not force:
            failed = [c["name"] for c in gates.get("checks", []) if not c.get("passed")]
            raise ConflictError(
                "Promotion gates did not pass; refusing to activate.",
                details={"failed_checks": failed},
            )
        now = utcnow()
        await self.session.execute(
            update(ModelVersion)
            .where(ModelVersion.status == ModelStatus.ACTIVE.value, ModelVersion.id != row.id)
            .values(status=ModelStatus.RETIRED.value, retired_at=now)
        )
        row.status, row.activated_at, row.retired_at = ModelStatus.ACTIVE.value, now, None
        await self.session.commit()
        self.container.model_store.invalidate()
        return row

    async def rollback(self) -> ModelVersion | None:
        """Retire the active model and reactivate the most recently retired one. With no
        previous model, inference simply falls back to the baseline."""
        active = (
            await self.session.scalars(
                select(ModelVersion).where(ModelVersion.status == ModelStatus.ACTIVE.value)
            )
        ).first()
        previous = (
            await self.session.scalars(
                select(ModelVersion)
                .where(ModelVersion.status == ModelStatus.RETIRED.value)
                .order_by(ModelVersion.retired_at.desc())
            )
        ).first()
        now = utcnow()
        if active is not None:
            active.status, active.retired_at = ModelStatus.RETIRED.value, now
        if previous is not None:
            previous.status, previous.activated_at = ModelStatus.ACTIVE.value, now
            previous.retired_at = None
        await self.session.commit()
        self.container.model_store.invalidate()
        return previous

    async def new_observations_since_last_training(self) -> int:
        latest = await self.session.scalar(select(func.max(ModelVersion.created_at)))
        stmt = select(func.count()).select_from(PriceObservation)
        if latest is not None:
            stmt = stmt.where(PriceObservation.ingested_at > latest)
        return int(await self.session.scalar(stmt) or 0)
