"""Forecast accountability: outcome evaluation and aggregate performance reporting.

Outcomes are computed purely from stored observations and each forecast's own cutoff,
so re-running evaluation always produces the same result. Only the outcome columns of
a forecast are written; the prediction itself is never touched.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.core.timeutil import utcnow
from app.forecasting.buckets import BUCKET_NAMES, bucket_for
from app.forecasting.evaluation.metrics import (
    binary_report,
    calibration_bins,
    policy_report,
    tier_report,
)
from app.forecasting.types import HORIZONS
from app.models import Forecast, ModelVersion, PriceObservation, Recommendation
from app.models.enums import ModelStatus

MAX_EVALUATION_ROWS = 50_000


def json_safe(value: Any) -> Any:
    """Replace NaN/inf and numpy scalars so the value is valid JSON for the database."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    return value


class PerformanceService:
    def __init__(self, session: AsyncSession, container: Container) -> None:
        self.session = session
        self.container = container

    # --- outcome evaluation ---------------------------------------------------

    async def evaluate_matured(self, *, now: datetime | None = None, limit: int = 2000) -> int:
        """Fill outcome fields for forecasts whose horizons have expired."""
        now = now or utcnow()
        shortest = now - timedelta(days=min(HORIZONS))
        forecasts = list(
            await self.session.scalars(
                select(Forecast)
                .where(Forecast.evaluation_complete.is_(False), Forecast.cutoff_at <= shortest)
                .order_by(Forecast.cutoff_at)
                .limit(limit)
            )
        )
        updated = 0
        for forecast in forecasts:
            if await self._evaluate(forecast, now):
                updated += 1
        await self.session.commit()
        return updated

    async def _evaluate(self, forecast: Forecast, now: datetime) -> bool:
        end = forecast.cutoff_at + timedelta(days=max(HORIZONS))
        rows = (
            await self.session.execute(
                select(
                    PriceObservation.observed_at,
                    PriceObservation.price_minor,
                    PriceObservation.discount_pct,
                )
                .where(
                    PriceObservation.game_id == forecast.game_id,
                    PriceObservation.shop_id == forecast.shop_id,
                    PriceObservation.country == forecast.country,
                    PriceObservation.currency == forecast.currency,
                    PriceObservation.observed_at > forecast.cutoff_at,
                    PriceObservation.observed_at <= end,
                )
                .order_by(PriceObservation.observed_at)
            )
        ).all()
        changed = False
        for horizon in HORIZONS:
            column = f"actual_sale_{horizon}d"
            horizon_end = forecast.cutoff_at + timedelta(days=horizon)
            if getattr(forecast, column) is not None or horizon_end > now:
                continue
            # "Discounted at any point inside the horizon": a sale already running at
            # the cutoff counts, as does any discounted observation inside the window.
            discounted = forecast.currently_on_sale or any(
                r.discount_pct > 0 and r.observed_at <= horizon_end for r in rows
            )
            setattr(forecast, column, bool(discounted))
            changed = True
            if horizon == 30:
                inside = [r.price_minor for r in rows if r.observed_at <= horizon_end]
                if forecast.current_price_minor is not None:
                    inside.append(forecast.current_price_minor)
                forecast.actual_min_price_30d_minor = min(inside) if inside else None
        if end <= now and not forecast.evaluation_complete:
            new_sales = [r for r in rows if r.discount_pct > 0]
            forecast.actual_max_discount_pct = (
                max(r.discount_pct for r in new_sales) if new_sales else None
            )
            prices = [r.price_minor for r in rows]
            if forecast.current_price_minor is not None:
                prices.append(forecast.current_price_minor)
            forecast.actual_min_price_minor = min(prices) if prices else None
            forecast.evaluation_complete = True
            changed = True
        if changed:
            forecast.evaluated_at = now
        return changed

    # --- reporting ------------------------------------------------------------

    async def _evaluated(self, model_version: str | None) -> list[Forecast]:
        stmt = select(Forecast).where(Forecast.actual_sale_7d.is_not(None))
        if model_version:
            stmt = stmt.where(Forecast.model_version == model_version)
        stmt = stmt.order_by(Forecast.cutoff_at.desc()).limit(MAX_EVALUATION_ROWS)
        return list(await self.session.scalars(stmt))

    @staticmethod
    def _pairs(forecasts: list[Forecast], horizon: int) -> tuple[list[float], list[float]]:
        y: list[float] = []
        p: list[float] = []
        for f in forecasts:
            actual = getattr(f, f"actual_sale_{horizon}d")
            if actual is not None:
                y.append(float(actual))
                p.append(float(getattr(f, f"sale_prob_{horizon}d")))
        return y, p

    async def by_horizon(self, model_version: str | None = None) -> dict[str, Any]:
        forecasts = await self._evaluated(model_version)
        threshold = self.container.policy_config.wait_min_probability
        return json_safe(
            {
                "model_version": model_version,
                "horizons": [
                    {"horizon_days": h, **binary_report(*self._pairs(forecasts, h), threshold)}
                    for h in HORIZONS
                ],
            }
        )

    async def calibration(
        self, horizon: int, model_version: str | None = None, bins: int = 10
    ) -> dict[str, Any]:
        y, p = self._pairs(await self._evaluated(model_version), horizon)
        return json_safe(
            {
                "model_version": model_version,
                "horizon_days": horizon,
                "n": len(y),
                "bins": calibration_bins(y, p, bins) if y else [],
            }
        )

    async def summary(self, model_version: str | None = None) -> dict[str, Any]:
        forecasts = await self._evaluated(model_version)
        total = int(await self.session.scalar(select(func.count()).select_from(Forecast)) or 0)
        threshold = self.container.policy_config.wait_min_probability
        by_method: dict[str, Any] = {}
        for method in sorted({f.method for f in forecasts}):
            subset = [f for f in forecasts if f.method == method]
            by_method[method] = {
                str(h): binary_report(*self._pairs(subset, h), threshold) for h in HORIZONS
            }

        # Discount-tier accuracy: complete forecasts where a new sale actually happened.
        tiers = [
            f
            for f in forecasts
            if f.evaluation_complete
            and f.actual_max_discount_pct is not None
            and not f.currently_on_sale
        ]
        tier_metrics: dict[str, Any] = {"n": 0}
        if tiers:
            matrix = np.array(
                [[f.discount_class_probs.get(name, 0.0) for name in BUCKET_NAMES] for f in tiers]
            )
            tier_metrics = tier_report(
                [bucket_for(f.actual_max_discount_pct or 0) for f in tiers],
                [float(f.actual_max_discount_pct or 0) for f in tiers],
                matrix,
            )
            covered = [
                f.price_lower_minor <= f.actual_min_price_minor <= f.price_upper_minor
                for f in tiers
                if f.price_lower_minor is not None
                and f.price_upper_minor is not None
                and f.actual_min_price_minor is not None
            ]
            tier_metrics["price_interval_coverage"] = (
                sum(covered) / len(covered) if covered else None
            )

        return json_safe(
            {
                "model_version": model_version,
                "forecasts_total": total,
                "forecasts_evaluated": len(forecasts),
                "sale_probability": {
                    str(h): binary_report(*self._pairs(forecasts, h), threshold) for h in HORIZONS
                },
                "by_method": by_method,
                "discount_tier": tier_metrics,
                "recommendation_policy": await self._policy(forecasts),
                "disclaimer": (
                    "Metrics describe past forecasts and do not guarantee future accuracy."
                ),
            }
        )

    async def _policy(self, forecasts: list[Forecast]) -> dict[str, Any]:
        """Realised outcomes of the generic 30-day recommendations."""
        usable = {
            f.id: f
            for f in forecasts
            if f.actual_min_price_30d_minor is not None and f.current_price_minor
        }
        if not usable:
            return {"n": 0}
        ids: list[uuid.UUID] = list(usable)
        recs: list[Recommendation] = []
        for start in range(0, len(ids), 500):
            recs.extend(
                await self.session.scalars(
                    select(Recommendation).where(
                        Recommendation.forecast_id.in_(ids[start : start + 500]),
                        Recommendation.max_wait_days == 30,
                    )
                )
            )
        seen: set[uuid.UUID] = set()
        actions, ratios, confidence, n_sales = [], [], [], []
        for rec in recs:
            if rec.forecast_id in seen:
                continue
            seen.add(rec.forecast_id)
            f = usable[rec.forecast_id]
            assert f.actual_min_price_30d_minor is not None and f.current_price_minor
            actions.append(rec.action)
            ratios.append(f.actual_min_price_30d_minor / f.current_price_minor)
            confidence.append(f.confidence_score)
            n_sales.append((f.feature_snapshot.get("features") or {}).get("n_sales_total") or 0)
        if not actions:
            return {"n": 0}
        return policy_report(actions, ratios, confidence, n_sales, 30)

    async def active_model(self) -> ModelVersion | None:
        stmt = (
            select(ModelVersion)
            .where(ModelVersion.status == ModelStatus.ACTIVE.value)
            .order_by(ModelVersion.activated_at.desc())
        )
        return (await self.session.scalars(stmt)).first()
