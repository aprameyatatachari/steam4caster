"""Offline training of the candidate model.

Model choice: LightGBM. It handles missing feature values natively (cold-start rows are
full of them), trains quickly on small tabular data, ships small self-contained wheels
on every platform, and exposes per-feature contribution values used for explanations.

Model A is one calibrated gradient-boosted classifier per horizon (7/30/90 days);
Model B is a multiclass classifier over discount tiers conditional on a sale.
Validation is forward-chaining with purging: no random splits, and training rows whose
label window would overlap the validation period are dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.isotonic import IsotonicRegression

from app.forecasting.buckets import BUCKET_NAMES
from app.forecasting.evaluation.metrics import (
    binary_report,
    brier_score,
    calibration_bins,
    policy_report,
    tier_report,
)
from app.forecasting.features.builder import FEATURE_NAMES
from app.forecasting.inference.engine import ML_TIER_WEIGHT
from app.forecasting.policy import PolicyConfig, PolicyInput, decide
from app.forecasting.training.gates import evaluate_gates
from app.forecasting.types import FEATURE_SCHEMA_VERSION, HORIZONS

DEFAULT_PARAMS: dict[str, Any] = {
    "n_estimators": 200,
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_child_samples": 20,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "random_state": 42,
    "n_jobs": 1,
    "verbose": -1,
}
N_FOLDS = 4
MIN_CALIBRATION_ROWS = 50
MIN_TIER_ROWS = 100
FEATURES = list(FEATURE_NAMES)
BASE_TIER_COLUMNS = [f"base_tier_{name}" for name in BUCKET_NAMES]


class InsufficientTrainingData(Exception):
    pass


class IdentityCalibrator:
    """Pass-through used when there is too little held-out data to fit a calibrator."""

    def predict(self, values: Any) -> np.ndarray:
        return np.clip(np.asarray(values, dtype=float), 0.0, 1.0)


@dataclass
class TrainingResult:
    payload: dict[str, Any]
    metrics: dict[str, Any]
    hyperparameters: dict[str, Any]
    training_cutoff: datetime
    gates_passed: bool


def time_folds(cutoffs: pd.Series, n_folds: int = N_FOLDS) -> list[tuple[Any, Any]]:
    """Forward-chaining ``(validation_start, validation_end]`` boundaries over time."""
    unique = np.sort(cutoffs.unique())
    if len(unique) < n_folds + 1:
        return []
    edges = [unique[int(len(unique) * i / (n_folds + 1)) - 1] for i in range(1, n_folds + 1)]
    edges.append(unique[-1])
    return [(edges[i], edges[i + 1]) for i in range(n_folds) if edges[i] < edges[i + 1]]


def _fit_calibrator(raw: np.ndarray, y: np.ndarray) -> Any:
    if len(raw) < MIN_CALIBRATION_ROWS or len(np.unique(y)) < 2:
        return IdentityCalibrator()
    return IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(raw, y)


def _out_of_fold_binary(
    frame: pd.DataFrame, horizon: int, folds: list[tuple[Any, Any]], params: dict[str, Any]
) -> pd.DataFrame:
    """Raw and progressively calibrated out-of-fold predictions for one horizon."""
    label = f"y_{horizon}"
    data = frame[frame[label].notna()]
    parts: list[pd.DataFrame] = []
    for fold, (val_start, val_end) in enumerate(folds):
        # Purge: a training label at cutoff c looks ahead ``horizon`` days, so c must
        # be at least that far before the validation period begins.
        train = data[data["cutoff"] + timedelta(days=horizon) <= val_start]
        valid = data[(data["cutoff"] > val_start) & (data["cutoff"] <= val_end)]
        if len(valid) == 0 or len(train) < 50 or train[label].nunique() < 2:
            continue
        model = LGBMClassifier(**params).fit(train[FEATURES], train[label].astype(int))
        raw = np.asarray(model.predict_proba(valid[FEATURES]))[:, 1]
        earlier = pd.concat(parts) if parts else None
        if earlier is not None:
            calibrator = _fit_calibrator(earlier["raw"].to_numpy(), earlier["y"].to_numpy())
        else:
            calibrator = IdentityCalibrator()
        parts.append(
            pd.DataFrame(
                {
                    "fold": fold,
                    "raw": raw,
                    "calibrated": calibrator.predict(raw),
                    "y": valid[label].to_numpy(),
                    # Only folds scored with a calibrator fitted on *earlier* folds count
                    # toward reported metrics.
                    "scored": earlier is not None,
                },
                index=valid.index,
            )
        )
    return pd.concat(parts) if parts else pd.DataFrame()


def _fit_tier(train: pd.DataFrame, params: dict[str, Any]) -> tuple[Any, list[str]] | None:
    classes = [name for name in BUCKET_NAMES if name in set(train["next_tier"])]
    if len(classes) < 2 or len(train) < MIN_TIER_ROWS:
        return None
    codes = train["next_tier"].map({name: i for i, name in enumerate(classes)}).astype(int)
    return LGBMClassifier(**params).fit(train[FEATURES], codes), classes


def _tier_matrix(model: Any, classes: list[str], rows: pd.DataFrame) -> np.ndarray:
    """Blend model tier probabilities with the baseline exactly as inference does."""
    predicted = np.asarray(model.predict_proba(rows[FEATURES]))
    full = np.zeros((len(rows), len(BUCKET_NAMES)))
    for column, name in enumerate(classes):
        full[:, BUCKET_NAMES.index(name)] = predicted[:, column]
    blended = ML_TIER_WEIGHT * full + (1 - ML_TIER_WEIGHT) * rows[BASE_TIER_COLUMNS].to_numpy()
    return blended / blended.sum(axis=1, keepdims=True)


def _policy_actions(
    rows: pd.DataFrame, probs: dict[int, np.ndarray], tiers: np.ndarray, max_wait: int
) -> list[str]:
    config = PolicyConfig()
    actions: list[str] = []
    for i, (_, row) in enumerate(rows.iterrows()):
        price = 10_000  # prices are scale-free here; only ratios matter
        ratio_regular = row["price_to_regular"] if row["price_to_regular"] > 0 else 1.0
        ratio_low = row["price_to_low"] if row["price_to_low"] and row["price_to_low"] > 0 else None
        decision = decide(
            PolicyInput(
                current_price_minor=price,
                regular_price_minor=round(price / ratio_regular),
                historical_low_minor=round(price / ratio_low) if ratio_low else None,
                current_discount_pct=int(row["current_discount"] or 0),
                currently_on_sale=False,
                new_sale_probs={h: float(probs[h][i]) for h in HORIZONS},
                tier_probs=dict(zip(BUCKET_NAMES, tiers[i], strict=True)),
                confidence=float(row["base_confidence"]),
                currency=None,
                max_wait_days=max_wait,
            ),
            config,
        )
        actions.append(decision.action)
    return actions


def evaluate_baseline(frame: pd.DataFrame) -> dict[str, Any]:
    """Baseline-only report over every labelled row (no model involved)."""
    report: dict[str, Any] = {"n_rows": len(frame), "sale": {}}
    for horizon in HORIZONS:
        data = frame[frame[f"y_{horizon}"].notna()]
        report["sale"][str(horizon)] = binary_report(
            data[f"y_{horizon}"], data[f"base_p_{horizon}"]
        )
        report["sale"][str(horizon)]["calibration"] = calibration_bins(
            data[f"y_{horizon}"], data[f"base_p_{horizon}"]
        )
    tiers = frame[frame["next_tier"].notna()]
    report["tier"] = tier_report(
        tiers["next_tier"].tolist(), tiers["next_discount"].tolist(),
        tiers[BASE_TIER_COLUMNS].to_numpy(),
    )  # fmt: skip
    known = frame[frame["min_ratio_30"].notna() & frame["y_90"].notna()]
    if len(known):
        probs = {h: known[f"base_p_{h}"].to_numpy() for h in HORIZONS}
        actions = _policy_actions(known, probs, known[BASE_TIER_COLUMNS].to_numpy(), 30)
        report["policy"] = policy_report(
            actions, known["min_ratio_30"], known["base_confidence"], known["n_sales_total"], 30
        )
    return report


def train_candidate(
    frame: pd.DataFrame, *, min_rows: int = 300, params: dict[str, Any] | None = None
) -> TrainingResult:
    params = {**DEFAULT_PARAMS, **(params or {})}
    if len(frame) < min_rows:
        raise InsufficientTrainingData(
            f"{len(frame)} rows available; at least {min_rows} are required to train."
        )
    folds = time_folds(frame["cutoff"])
    if len(folds) < 2:
        raise InsufficientTrainingData("not enough distinct cutoff dates for forward validation")

    metrics: dict[str, Any] = {
        "n_rows": len(frame),
        "n_series": int(frame["series_key"].nunique()),
        "n_folds": len(folds),
        "validation": "forward-chaining with purged label overlap",
        "sale": {},
        "calibration": {},
    }
    sale_models: dict[int, Any] = {}
    calibrators: dict[int, Any] = {}
    ece: dict[int, float] = {}
    oof: dict[int, pd.DataFrame] = {}

    for horizon in HORIZONS:
        label = f"y_{horizon}"
        data = frame[frame[label].notna()]
        if len(data) < min_rows // 2 or data[label].nunique() < 2:
            raise InsufficientTrainingData(f"too few labelled rows for the {horizon}-day horizon")
        predictions = _out_of_fold_binary(frame, horizon, folds, params)
        scored = predictions[predictions["scored"]] if len(predictions) else predictions
        if len(scored) < MIN_CALIBRATION_ROWS or scored["y"].nunique() < 2:
            raise InsufficientTrainingData(
                f"too little held-out data to validate the {horizon}-day horizon"
            )
        oof[horizon] = scored
        held_out = frame.loc[scored.index]
        candidate = binary_report(scored["y"], scored["calibrated"])
        baseline = binary_report(scored["y"], held_out[f"base_p_{horizon}"])
        cohorts: dict[str, Any] = {}
        for name, mask in (
            ("sparse_lt_4_sales", held_out["n_sales_total"] < 4),
            ("rich_ge_4_sales", held_out["n_sales_total"] >= 4),
        ):
            if mask.sum():
                cohorts[name] = {
                    "n": int(mask.sum()),
                    "candidate_brier": brier_score(scored["y"][mask], scored["calibrated"][mask]),
                    "baseline_brier": brier_score(
                        scored["y"][mask], held_out[f"base_p_{horizon}"][mask]
                    ),
                }
        metrics["sale"][str(horizon)] = {
            "candidate": candidate, "baseline": baseline, "cohorts": cohorts
        }  # fmt: skip
        metrics["calibration"][str(horizon)] = calibration_bins(scored["y"], scored["calibrated"])
        ece[horizon] = candidate["ece"]
        # Final model: all labelled rows; final calibrator: every out-of-fold prediction.
        sale_models[horizon] = LGBMClassifier(**params).fit(data[FEATURES], data[label].astype(int))
        calibrators[horizon] = _fit_calibrator(
            predictions["raw"].to_numpy(), predictions["y"].to_numpy()
        )

    # --- Model B: discount tier conditional on a sale ----------------------
    tier_rows = frame[frame["next_tier"].notna()]
    tier_params = {**params, "min_child_samples": max(5, params["min_child_samples"] // 2)}
    tier_parts: list[tuple[pd.DataFrame, np.ndarray]] = []
    for val_start, val_end in folds:
        train = tier_rows[tier_rows["cutoff"] + timedelta(days=180) <= val_start]
        valid = tier_rows[(tier_rows["cutoff"] > val_start) & (tier_rows["cutoff"] <= val_end)]
        fitted = _fit_tier(train, tier_params) if len(valid) else None
        if fitted is not None:
            tier_parts.append((valid, _tier_matrix(fitted[0], fitted[1], valid)))
    final_tier = _fit_tier(tier_rows, tier_params)
    tier_matrix_by_index: dict[Any, np.ndarray] = {}
    if tier_parts and final_tier is not None:
        held = pd.concat([part for part, _ in tier_parts])
        matrix = np.vstack([m for _, m in tier_parts])
        tier_matrix_by_index = dict(zip(held.index, matrix, strict=True))
        metrics["tier"] = {
            "candidate": tier_report(
                held["next_tier"].tolist(), held["next_discount"].tolist(), matrix
            ),
            "baseline": tier_report(
                held["next_tier"].tolist(), held["next_discount"].tolist(),
                held[BASE_TIER_COLUMNS].to_numpy(),
            ),
        }  # fmt: skip
    else:
        final_tier = None
        metrics["tier"] = {"skipped": "too few completed sales to validate a tier model"}

    # --- Policy simulation on rows held out for every horizon ---------------
    common = oof[7].index.intersection(oof[30].index).intersection(oof[90].index)
    held_policy = frame.loc[common]
    held_policy = held_policy[held_policy["min_ratio_30"].notna()]
    if len(held_policy):
        idx = held_policy.index
        base_tiers = held_policy[BASE_TIER_COLUMNS].to_numpy()
        cand_tiers = np.vstack(
            [tier_matrix_by_index.get(i, base_tiers[n]) for n, i in enumerate(idx)]
        )
        cand_probs = {h: oof[h].loc[idx, "calibrated"].to_numpy() for h in HORIZONS}
        base_probs = {h: held_policy[f"base_p_{h}"].to_numpy() for h in HORIZONS}
        common_args = (
            held_policy["min_ratio_30"], held_policy["base_confidence"],
            held_policy["n_sales_total"], 30,
        )  # fmt: skip
        metrics["policy"] = {
            "candidate": policy_report(
                _policy_actions(held_policy, cand_probs, cand_tiers, 30), *common_args
            ),
            "baseline": policy_report(
                _policy_actions(held_policy, base_probs, base_tiers, 30), *common_args
            ),
        }

    metrics["gates"] = evaluate_gates(metrics)
    ranges = {
        name: (float(np.nanpercentile(frame[name], 1)), float(np.nanpercentile(frame[name], 99)))
        for name in FEATURES
        if frame[name].notna().any()
    }
    payload = {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": FEATURES,
        "sale_models": sale_models,
        "calibrators": calibrators,
        "tier_model": final_tier[0] if final_tier else None,
        "tier_classes": final_tier[1] if final_tier else [],
        "feature_ranges": ranges,
        "ece": ece,
    }
    return TrainingResult(
        payload=payload,
        metrics=metrics,
        hyperparameters=params,
        training_cutoff=pd.Timestamp(frame["cutoff"].max()).to_pydatetime(),
        gates_passed=bool(metrics["gates"]["passed"]),
    )
