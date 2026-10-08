"""Promotion gates: a candidate must beat the deterministic baseline on proper scoring
rules *and* stay calibrated *and* not regress on any history cohort. One improved
aggregate number is never sufficient."""

from __future__ import annotations

from typing import Any

from app.forecasting.types import HORIZONS

ECE_ABSOLUTE_OK = 0.05
ECE_SLACK = 0.01
COHORT_REGRESSION_TOLERANCE = 1.05
COHORT_MIN_ROWS = 50
TIER_WITHIN_ONE_SLACK = 0.02
TIER_MAE_TOLERANCE = 1.05


def _check(name: str, passed: bool, candidate: Any, baseline: Any, rule: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "candidate": candidate,
        "baseline": baseline,
        "rule": rule,
    }


def evaluate_gates(metrics: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        block = metrics["sale"][str(horizon)]
        cand, base = block["candidate"], block["baseline"]
        checks.append(
            _check(
                f"brier_{horizon}d", cand["brier"] <= base["brier"], cand["brier"], base["brier"],
                "candidate Brier score must not exceed the baseline",
            )
        )  # fmt: skip
        limit = max(ECE_ABSOLUTE_OK, base["ece"] + ECE_SLACK)
        checks.append(
            _check(
                f"calibration_{horizon}d", cand["ece"] <= limit, cand["ece"], base["ece"],
                f"candidate ECE must be <= max({ECE_ABSOLUTE_OK}, baseline + {ECE_SLACK})",
            )
        )  # fmt: skip
        for cohort, values in block.get("cohorts", {}).items():
            if values["n"] < COHORT_MIN_ROWS:
                continue
            checks.append(
                _check(
                    f"cohort_{cohort}_{horizon}d",
                    values["candidate_brier"]
                    <= values["baseline_brier"] * COHORT_REGRESSION_TOLERANCE,
                    values["candidate_brier"], values["baseline_brier"],
                    f"cohort Brier must be within {COHORT_REGRESSION_TOLERANCE}x of baseline",
                )
            )  # fmt: skip
    cand30, base30 = metrics["sale"]["30"]["candidate"], metrics["sale"]["30"]["baseline"]
    checks.append(
        _check(
            "log_loss_30d", cand30["log_loss"] <= base30["log_loss"], cand30["log_loss"],
            base30["log_loss"], "candidate 30-day log loss must not exceed the baseline",
        )
    )  # fmt: skip
    tier = metrics.get("tier") or {}
    if tier.get("candidate", {}).get("n") and tier.get("baseline", {}).get("n"):
        cand_t, base_t = tier["candidate"], tier["baseline"]
        checks.append(
            _check(
                "tier_within_one_bucket",
                cand_t["within_one_bucket_accuracy"]
                >= base_t["within_one_bucket_accuracy"] - TIER_WITHIN_ONE_SLACK,
                cand_t["within_one_bucket_accuracy"], base_t["within_one_bucket_accuracy"],
                f"within-one-bucket accuracy may drop at most {TIER_WITHIN_ONE_SLACK}",
            )
        )  # fmt: skip
        checks.append(
            _check(
                "tier_discount_mae",
                cand_t["discount_mae"] <= base_t["discount_mae"] * TIER_MAE_TOLERANCE,
                cand_t["discount_mae"], base_t["discount_mae"],
                f"discount MAE must be within {TIER_MAE_TOLERANCE}x of baseline",
            )
        )  # fmt: skip
    return {"passed": all(c["passed"] for c in checks), "checks": checks}
