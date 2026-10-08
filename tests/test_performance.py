from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import numpy as np
from sqlalchemy import select

from app.core.container import Container
from app.core.timeutil import utcnow
from app.models import Forecast
from app.services.catalog import CatalogService
from app.services.forecasts import ForecastService
from app.services.performance import PerformanceService, json_safe
from app.services.prices import PriceService
from tests.helpers import ScriptedProvider, price_points, regular_sales, to_history

PREDICTION_FIELDS = (
    "sale_prob_7d", "sale_prob_30d", "sale_prob_90d", "new_sale_prob_30d", "discount_class_probs",
    "expected_discount_pct", "price_lower_minor", "price_median_minor", "price_upper_minor",
    "window_start", "window_end", "confidence_score", "data_quality", "explanation_factors",
    "model_version", "method", "cutoff_at", "created_at", "feature_snapshot",
)  # fmt: skip


async def seed_forecasts(container: Container, days_ago: tuple[int, ...]) -> list[Forecast]:
    """Create forecasts dated in the past against a history that continues after them."""
    provider = ScriptedProvider()
    now = utcnow()
    # Week-long sales every 60 days at 50% off; the most recent one started 20 days ago.
    provider.series["US"] = to_history(price_points(now, sales=regular_sales(8, 60, 20)))
    container.price_provider = provider
    async with container.db.session() as session:
        catalog = CatalogService(session, container)
        game = await catalog.ensure_info((await catalog.search("script"))[0])
        shop = await catalog.steam_shop()
        await PriceService(session, container).ingest_history(game, shop, "US")
        service = ForecastService(session, container)
        forecasts = []
        for days in days_ago:
            forecast = await service.generate(game, shop, "US", now=now - timedelta(days=days))
            await service.recommend(forecast, max_wait_days=30)
            forecasts.append(forecast)
        return forecasts


async def test_matured_forecasts_are_evaluated_without_touching_predictions(
    container: Container,
) -> None:
    forecasts = await seed_forecasts(container, (100, 50, 25, 18, 3))
    async with container.db.session() as session:
        before = {
            f.id: {name: getattr(f, name) for name in PREDICTION_FIELDS}
            for f in await session.scalars(select(Forecast))
        }
        service = PerformanceService(session, container)
        assert await service.evaluate_matured() == 4  # the 3-day-old forecast is too young
        rows = {f.id: f for f in await session.scalars(select(Forecast))}
        for forecast_id, snapshot in before.items():
            for name, value in snapshot.items():
                assert getattr(rows[forecast_id], name) == value, name  # predictions immutable

        oldest, mid, recent, during, newest = (rows[f.id] for f in forecasts)
        # 100 days ago the sale that began 80 days ago was still 20 days away.
        assert (oldest.actual_sale_7d, oldest.actual_sale_30d, oldest.actual_sale_90d) == (
            False, True, True,
        )  # fmt: skip
        assert oldest.evaluation_complete and oldest.actual_max_discount_pct == 50
        assert oldest.actual_min_price_minor == 1000 and oldest.actual_min_price_30d_minor == 1000
        # 50 days ago the next sale (20 days ago) was exactly 30 days away; 90d is unknown.
        assert mid.actual_sale_7d is False and mid.actual_sale_30d is True
        assert mid.actual_sale_90d is None and not mid.evaluation_complete
        # 25 days ago the sale began 5 days later; the 30-day horizon has not expired.
        assert recent.actual_sale_7d is True and recent.actual_sale_30d is None
        # 18 days ago a sale was running, which counts as discounted inside the horizon.
        assert during.currently_on_sale and during.actual_sale_7d is True
        assert newest.actual_sale_7d is None and newest.evaluated_at is None

        # Re-running is reproducible: nothing new to write and nothing changes.
        snapshot = {
            f.id: (f.actual_sale_7d, f.actual_sale_30d, f.evaluated_at) for f in rows.values()
        }
        assert await service.evaluate_matured() == 0
        again = {
            f.id: (f.actual_sale_7d, f.actual_sale_30d, f.evaluated_at)
            for f in await session.scalars(select(Forecast))
        }
        assert again == snapshot


async def test_outcomes_ignore_observations_in_another_currency(container: Container) -> None:
    (forecast,) = await seed_forecasts(container, (40,))
    async with container.db.session() as session:
        row = await session.get(Forecast, forecast.id)
        assert row is not None
        row.currency = "EUR"  # as if the forecast belonged to a different currency era
        await session.commit()
        await PerformanceService(session, container).evaluate_matured()
        await session.refresh(row)
        assert row.actual_sale_30d is False  # USD observations were not counted


async def test_performance_endpoints(
    client: httpx.AsyncClient, auth: dict[str, str], container: Container
) -> None:
    forecasts = await seed_forecasts(container, (200, 160, 130, 100, 70, 40, 20, 9))
    game_id: uuid.UUID = forecasts[0].game_id
    async with container.db.session() as session:
        await PerformanceService(session, container).evaluate_matured()

    summary = (await client.get("/api/v1/model-performance/summary", headers=auth)).json()
    assert summary["forecasts_total"] == 8 and summary["forecasts_evaluated"] == 8
    seven = summary["sale_probability"]["7"]
    assert seven["n"] == 8 and 0 <= seven["brier"] <= 1 and seven["log_loss"] >= 0
    assert set(summary["by_method"]) == {"BASELINE"}
    assert summary["recommendation_policy"]["overall"]["n"] >= 5
    assert "do not guarantee" in summary["disclaimer"]

    by_horizon = (await client.get("/api/v1/model-performance/by-horizon", headers=auth)).json()
    counts = {h["horizon_days"]: h["n"] for h in by_horizon["horizons"]}
    assert counts[7] == 8 and counts[7] >= counts[30] >= counts[90] >= 1

    calibration = (
        await client.get(
            "/api/v1/model-performance/calibration", params={"horizon_days": 7, "bins": 5},
            headers=auth,
        )
    ).json()  # fmt: skip
    assert calibration["n"] == 8 and sum(b["count"] for b in calibration["bins"]) == 8
    for b in calibration["bins"]:
        assert b["bin_lower"] <= b["mean_predicted"] <= b["bin_upper"]
    bad = await client.get(
        "/api/v1/model-performance/calibration", params={"horizon_days": 14}, headers=auth
    )
    assert bad.status_code == 422

    other = (
        await client.get(
            "/api/v1/model-performance/summary", params={"model_version": "nope"}, headers=auth
        )
    ).json()
    assert other["forecasts_evaluated"] == 0 and other["sale_probability"]["7"] == {"n": 0}

    active = (await client.get("/api/v1/model-versions/active", headers=auth)).json()
    assert active["active"] is None and active["fallback_method"] == "BASELINE"
    assert active["baseline_version"] == "baseline-1" and active["ruleset_version"] == "rules-1"

    history = (
        await client.get(
            f"/api/v1/games/{game_id}/forecast-history", params={"limit": 3}, headers=auth
        )
    ).json()
    assert len(history["items"]) == 3 and history["next_cursor"]
    assert history["items"][-1]["outcome"] is not None
    assert "feature_snapshot" not in str(history)  # private snapshots never leave the API
    stamps = [f["created_at"] for f in history["items"]]
    assert stamps == sorted(stamps, reverse=True)
    rest = (
        await client.get(
            f"/api/v1/games/{game_id}/forecast-history",
            params={"limit": 100, "cursor": history["next_cursor"]}, headers=auth,
        )
    ).json()  # fmt: skip
    assert len(rest["items"]) == 5 and rest["next_cursor"] is None
    assert not {f["id"] for f in rest["items"]} & {f["id"] for f in history["items"]}


def test_json_safe_removes_non_finite_numbers() -> None:
    cleaned = json_safe({"a": float("nan"), "b": [np.float64(1.5), float("inf")], "c": np.int64(3)})
    assert cleaned == {"a": None, "b": [1.5, None], "c": 3}
