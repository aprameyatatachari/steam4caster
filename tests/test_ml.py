from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.core.container import Container
from app.core.errors import ConflictError
from app.forecasting.datasets.builder import SeriesData, build_dataset, prior_index_for
from app.forecasting.evaluation.metrics import (
    binary_report,
    calibration_bins,
    expected_calibration_error,
    policy_report,
    tier_report,
)
from app.forecasting.features.builder import FEATURE_NAMES, build_state
from app.forecasting.inference.registry import (
    ArtifactError,
    file_checksum,
    load_artifact,
    save_artifact,
)
from app.forecasting.training import trainer
from app.forecasting.training.gates import evaluate_gates
from app.forecasting.training.trainer import (
    InsufficientTrainingData,
    evaluate_baseline,
    time_folds,
    train_candidate,
)
from app.forecasting.types import GameContext, PricePoint
from app.models import ModelVersion
from app.models.enums import ModelStatus
from app.providers.pricing.fake import FakePriceProvider
from app.services.catalog import CatalogService
from app.services.forecasts import ForecastService
from app.services.models import ModelService
from app.services.prices import PriceService

AS_OF = datetime(2026, 9, 1, tzinfo=UTC)
FAST_PARAMS = {"n_estimators": 40, "num_leaves": 7}


@pytest.fixture(scope="module")
def series() -> list[SeriesData]:
    """Synthetic multi-region series from the deterministic fake provider."""
    provider = FakePriceProvider(clock=lambda: AS_OF)
    result: list[SeriesData] = []

    async def collect() -> None:
        for provider_id in provider.catalog_ids():
            info = await provider.get_game_info(provider_id)
            assert info is not None
            for country in ("US", "IN"):
                history = await provider.get_price_history(
                    provider_id, country, [61], datetime(2010, 1, 1, tzinfo=UTC)
                )
                if not history:
                    continue
                result.append(
                    SeriesData(
                        series_key=f"{provider_id}:{country}",
                        game_key=provider_id,
                        points=[
                            PricePoint(p.timestamp, p.price.amount_minor, p.regular.amount_minor, p.cut)
                            for p in history
                        ],
                        context=GameContext(
                            release_date=info.release_date, publisher=info.publishers[0],
                            primary_tag=info.tags[0], type=info.type,
                        ),
                    )
                )  # fmt: skip

    asyncio.run(collect())
    return result


@pytest.fixture(scope="module")
def frame(series: list[SeriesData]) -> pd.DataFrame:
    return build_dataset(series, AS_OF, step_days=14)


# --- dataset ----------------------------------------------------------------


def test_dataset_has_features_labels_and_baseline_columns(frame: pd.DataFrame) -> None:
    assert len(frame) > 1500
    assert set(FEATURE_NAMES) <= set(frame.columns)
    assert {"y_7", "y_30", "y_90", "next_tier", "base_p_30", "min_ratio_30"} <= set(frame.columns)
    assert frame["cutoff"].is_monotonic_increasing
    assert (frame["current_discount"] == 0).all()  # never sampled while a sale is running
    labelled = frame[frame["y_30"].notna()]
    assert 0.05 < labelled["y_30"].mean() < 0.95


def test_labels_are_missing_when_the_horizon_is_not_yet_known(frame: pd.DataFrame) -> None:
    for horizon in (7, 30, 90):
        unknown = frame["cutoff"] + timedelta(days=horizon) > AS_OF
        assert frame.loc[unknown, f"y_{horizon}"].isna().all()
        assert frame.loc[~unknown, f"y_{horizon}"].notna().all()
        assert frame.loc[unknown, f"min_ratio_{horizon}"].isna().all()
    # Horizons are nested, so a sale within 7 days is also within 30 and 90.
    known = frame[frame["y_90"].notna()]
    assert (known["y_7"] <= known["y_30"]).all() and (known["y_30"] <= known["y_90"]).all()


def test_dataset_rows_match_features_built_from_truncated_history(
    series: list[SeriesData], frame: pd.DataFrame
) -> None:
    """Leakage check on real rows: rebuilding from only-past data gives identical features."""
    index = prior_index_for(series)
    by_key = {s.series_key: s for s in series}
    sample = frame.sample(n=40, random_state=7)
    for _, row in sample.iterrows():
        item = by_key[row["series_key"]]
        cutoff = row["cutoff"].to_pydatetime()
        past_only = [p for p in item.points if p.at <= cutoff]
        prior = index.prior_for(item.context.publisher, item.context.primary_tag, cutoff)
        state = build_state(past_only, item.context, cutoff, prior)
        for name in FEATURE_NAMES:
            expected, actual = state.features[name], row[name]
            if expected is None:
                assert math.isnan(actual), name
            else:
                assert actual == pytest.approx(expected), name


def test_cohort_priors_only_use_sales_before_the_cutoff(series: list[SeriesData]) -> None:
    index = prior_index_for(series)
    early = index.prior_for("Kestrel Interactive", "Racing", datetime(2018, 6, 15, tzinfo=UTC))
    late = index.prior_for("Kestrel Interactive", "Racing", datetime(2026, 6, 15, tzinfo=UTC))
    assert late.n_events > early.n_events
    nothing = index.prior_for("Kestrel Interactive", "Racing", datetime(2016, 1, 15, tzinfo=UTC))
    assert nothing.level == "GLOBAL_DEFAULT"  # nothing had happened yet


def test_time_folds_move_forward_and_never_overlap(frame: pd.DataFrame) -> None:
    folds = time_folds(frame["cutoff"])
    assert len(folds) == 4
    for (start, end), (next_start, _) in zip(folds, folds[1:], strict=False):
        assert start < end == next_start
    assert time_folds(frame["cutoff"].head(3)) == []


# --- metrics ----------------------------------------------------------------


def test_probability_metrics() -> None:
    y = [1, 1, 0, 0]
    perfect = binary_report(y, [1.0, 1.0, 0.0, 0.0])
    assert perfect["brier"] == 0 and perfect["roc_auc"] == 1 and perfect["ece"] == 0
    uninformed = binary_report(y, [0.5] * 4)
    assert uninformed["brier"] == pytest.approx(0.25)
    assert uninformed["log_loss"] == pytest.approx(math.log(2))
    overconfident = [1.0] * 4
    assert expected_calibration_error(y, overconfident) == pytest.approx(0.5)
    bins = calibration_bins(y, [0.05, 0.95, 0.05, 0.95], n_bins=10)
    assert [(b["count"], b["observed_rate"]) for b in bins] == [(2, 0.5), (2, 0.5)]
    one_class = binary_report([1, 1], [0.9, 0.8])
    assert one_class["roc_auc"] is None  # undefined with a single class
    thresholded = binary_report([1, 0, 1, 0], [0.9, 0.7, 0.4, 0.1], threshold=0.6)
    assert thresholded["precision_at_threshold"] == 0.5
    assert thresholded["recall_at_threshold"] == 0.5


def test_tier_metrics() -> None:
    probs = np.array([[0, 0, 0, 0, 1, 0, 0], [0, 0, 0, 0, 1, 0, 0], [1, 0, 0, 0, 0, 0, 0]], float)
    report = tier_report(["50_TO_59", "60_TO_74", "75_PLUS"], [50, 66, 80], probs)
    assert report["accuracy"] == pytest.approx(1 / 3)
    assert report["within_one_bucket_accuracy"] == pytest.approx(2 / 3)
    assert report["discount_mae"] > 0 and 0 <= report["interval_coverage"] <= 1
    assert set(report["class_f1"]) == {"50_TO_59", "60_TO_74", "75_PLUS"}


def test_policy_report_measures_savings_and_regret() -> None:
    report = policy_report(
        ["WAIT", "WAIT", "BUY", "BUY", "NEUTRAL"], [0.5, 1.0, 0.6, 1.0, 0.8],
        [0.8, 0.8, 0.3, 0.3, 0.5], [6, 6, 1, 1, 6], 30,
    )  # fmt: skip
    overall = report["overall"]
    assert overall["wait_followed_by_lower_price_rate"] == 0.5
    assert overall["avg_realized_savings_fraction"] == pytest.approx(0.1)  # 0.5 saved on 1 of 5
    assert overall["buy_regret_rate"] == 0.5  # one BUY preceded a 40% drop
    assert report["by_confidence_band"]["high"]["counts"]["WAIT"] == 2
    assert report["by_history_cohort"]["sparse_lt_4_sales"]["counts"]["BUY"] == 2


# --- training and gates -----------------------------------------------------


@pytest.fixture(scope="module")
def trained(frame: pd.DataFrame) -> trainer.TrainingResult:
    return train_candidate(frame, min_rows=300, params=FAST_PARAMS)


def test_training_compares_candidate_with_baseline(trained: trainer.TrainingResult) -> None:
    metrics = trained.metrics
    assert metrics["validation"].startswith("forward-chaining")
    for horizon in ("7", "30", "90"):
        block = metrics["sale"][horizon]
        assert block["candidate"]["n"] == block["baseline"]["n"] > 100
        for side in ("candidate", "baseline"):
            assert 0 <= block[side]["brier"] <= 1 and block[side]["ece"] >= 0
        assert set(block["cohorts"]) <= {"sparse_lt_4_sales", "rich_ge_4_sales"}
    assert metrics["tier"]["candidate"]["n"] == metrics["tier"]["baseline"]["n"] > 0
    assert metrics["policy"]["candidate"]["overall"]["n"] > 0
    assert isinstance(trained.gates_passed, bool)
    assert {c["name"] for c in metrics["gates"]["checks"]} >= {
        "brier_30d", "calibration_30d", "log_loss_30d", "tier_within_one_bucket",
    }  # fmt: skip
    assert trained.payload["feature_names"] == list(FEATURE_NAMES)
    assert trained.training_cutoff <= AS_OF


def test_training_refuses_too_little_data(frame: pd.DataFrame) -> None:
    with pytest.raises(InsufficientTrainingData):
        train_candidate(frame.head(100), min_rows=300)
    one_class = frame.copy()
    one_class["y_30"] = 0.0
    with pytest.raises(InsufficientTrainingData):
        train_candidate(one_class, min_rows=300, params=FAST_PARAMS)


def test_baseline_evaluation_report(frame: pd.DataFrame) -> None:
    report = evaluate_baseline(frame)
    assert report["sale"]["30"]["n"] > 1000 and report["sale"]["30"]["calibration"]
    assert report["tier"]["n"] > 0 and report["policy"]["overall"]["n"] > 0


def _metrics(cand_brier: float, cand_ece: float, sparse_cand: float = 0.1) -> dict:
    def block() -> dict:
        return {
            "candidate": {"brier": cand_brier, "ece": cand_ece, "log_loss": 0.4},
            "baseline": {"brier": 0.20, "ece": 0.08, "log_loss": 0.5},
            "cohorts": {
                "sparse_lt_4_sales": {
                    "n": 200,
                    "candidate_brier": sparse_cand,
                    "baseline_brier": 0.1,
                },
                "tiny": {
                    "n": 5,
                    "candidate_brier": 0.9,
                    "baseline_brier": 0.1,
                },  # too small to judge
            },
        }

    return {"sale": {"7": block(), "30": block(), "90": block()}, "tier": {"skipped": "x"}}


def test_gates_require_more_than_one_improved_metric() -> None:
    assert evaluate_gates(_metrics(0.15, 0.04))["passed"]
    worse_brier = evaluate_gates(_metrics(0.25, 0.04))
    assert not worse_brier["passed"]
    assert "brier_30d" in {c["name"] for c in worse_brier["checks"] if not c["passed"]}
    # Better Brier score but badly calibrated: still rejected.
    miscalibrated = evaluate_gates(_metrics(0.15, 0.15))
    assert {c["name"] for c in miscalibrated["checks"] if not c["passed"]} == {
        "calibration_7d", "calibration_30d", "calibration_90d",
    }  # fmt: skip
    # Better overall but regressing on the sparse-history cohort: still rejected.
    cohort = evaluate_gates(_metrics(0.15, 0.04, sparse_cand=0.2))
    assert not cohort["passed"]
    assert all("tiny" not in c["name"] for c in cohort["checks"])


# --- artifacts --------------------------------------------------------------


def test_artifact_roundtrip_and_integrity_checks(
    trained: trainer.TrainingResult, tmp_path: Path
) -> None:
    path, checksum = save_artifact(tmp_path, "lgbm-test", trained.payload)
    assert checksum == file_checksum(Path(path)) and len(checksum) == 64
    loaded = load_artifact(path, checksum)
    assert loaded.version == "lgbm-test" and set(loaded.sale_models) == {7, 30, 90}

    with pytest.raises(ArtifactError, match="checksum_mismatch"):
        load_artifact(path, "0" * 64)
    with pytest.raises(ArtifactError, match="artifact_missing"):
        load_artifact(tmp_path / "nope.joblib", checksum)
    stale_path, stale_sum = save_artifact(
        tmp_path, "lgbm-old", {**trained.payload, "feature_schema_version": "fs-0"}
    )
    with pytest.raises(ArtifactError, match="feature_schema_mismatch"):
        load_artifact(stale_path, stale_sum)
    renamed_path, renamed_sum = save_artifact(
        tmp_path, "lgbm-cols", {**trained.payload, "feature_names": ["a", "b"]}
    )
    with pytest.raises(ArtifactError, match="feature_names_mismatch"):
        load_artifact(renamed_path, renamed_sum)


# --- lifecycle against the database ----------------------------------------


async def test_train_activate_infer_rollback_and_fallback(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(trainer.DEFAULT_PARAMS, "n_estimators", 30)
    monkeypatch.setitem(trainer.DEFAULT_PARAMS, "num_leaves", 7)
    async with container.db.session() as session:
        catalog = CatalogService(session, container)
        shop = await catalog.steam_shop()
        prices = PriceService(session, container)
        games = []
        for app_id in (900001, 900003, 900004, 900006, 900007, 900008, 900009, 900010):
            game = await catalog.lookup(steam_app_id=app_id)
            for country in ("US", "IN"):
                await prices.ingest_history(game, shop, country)
            games.append(game)
        target = games[1]
        forecasts = ForecastService(session, container)
        models = ModelService(session, container)

        before = await forecasts.generate(target, shop, "US")
        assert before.method == "BASELINE" and before.model_version_id is None
        assert before.feature_snapshot["fallback_reason"] == "no_active_model"

        candidate = await models.train(shop)
        assert candidate.status == ModelStatus.CANDIDATE
        assert Path(candidate.artifact_uri).is_file()
        assert candidate.artifact_checksum == file_checksum(Path(candidate.artifact_uri))
        assert candidate.metrics["sale"]["30"]["baseline"]["brier"] >= 0
        assert candidate.training_cutoff is not None

        # A candidate is never used for inference until it is explicitly activated.
        assert (await forecasts.generate(target, shop, "US")).method == "BASELINE"

        # Promotion is refused when gates fail, unless forced.
        original_metrics = dict(candidate.metrics)
        candidate.metrics = {**original_metrics, "gates": {"passed": False, "checks": [
            {"name": "brier_30d", "passed": False}]}}  # fmt: skip
        await session.commit()
        with pytest.raises(ConflictError) as excinfo:
            await models.activate(candidate.version)
        assert excinfo.value.details == {"failed_checks": ["brier_30d"]}
        candidate.metrics = {**original_metrics, "gates": {"passed": True, "checks": []}}
        await session.commit()
        active = await models.activate(candidate.version)
        assert active.status == ModelStatus.ACTIVE and active.activated_at is not None

        with_ml = await forecasts.generate(target, shop, "US")
        assert with_ml.method == "ML" and with_ml.model_version == candidate.version
        assert with_ml.model_version_id == candidate.id
        assert (
            0 <= with_ml.new_sale_prob_7d <= with_ml.new_sale_prob_30d <= with_ml.new_sale_prob_90d
        )
        assert sum(with_ml.discount_class_probs.values()) == pytest.approx(1.0, abs=1e-4)
        assert all(p > 0 for p in with_ml.discount_class_probs.values())
        assert with_ml.explanation_factors
        # Earlier forecasts are untouched by the new model.
        await session.refresh(before)
        assert before.method == "BASELINE"

        # Cold-start series still use the hierarchical baseline even with a model active.
        newcomer = await catalog.lookup(steam_app_id=900012)
        await prices.ingest_history(newcomer, shop, "US")
        cold = await forecasts.generate(newcomer, shop, "US")
        assert cold.method == "BASELINE"
        assert cold.feature_snapshot["fallback_reason"] == "insufficient_history"

        # Rollback with no previous model returns to the baseline.
        assert await models.rollback() is None
        assert (await forecasts.generate(target, shop, "US")).method == "BASELINE"
        await session.refresh(candidate)
        assert candidate.status == ModelStatus.RETIRED

        # A second model can be activated and rolled back to the first.
        second = await models.train(shop, as_of=datetime.now(UTC) + timedelta(seconds=2))
        await models.activate(candidate.version, force=True)
        await models.activate(second.version, force=True)
        statuses = {m.version: m.status for m in await session.scalars(select(ModelVersion))}
        assert statuses == {candidate.version: "RETIRED", second.version: "ACTIVE"}
        restored = await models.rollback()
        assert restored is not None and restored.version == candidate.version
        assert (await forecasts.generate(target, shop, "US")).model_version == candidate.version

        # A corrupted artifact must not break forecasting: inference falls back safely.
        Path(candidate.artifact_uri).write_bytes(b"corrupted")
        container.model_store.invalidate()  # as a freshly started process would see it
        fallback = await forecasts.generate(target, shop, "US")
        assert fallback.method == "BASELINE" and fallback.model_version == "baseline-1"
