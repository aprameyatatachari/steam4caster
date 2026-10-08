"""Evaluation metrics for sale-probability, discount-tier and policy outputs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import ArrayLike
from sklearn.metrics import average_precision_score, f1_score, roc_auc_score

from app.forecasting.buckets import BUCKET_NAMES, BUCKETS

EPS = 1e-6


def _arrays(y_true: ArrayLike, y_prob: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray(y_true, dtype=float), np.clip(np.asarray(y_prob, dtype=float), 0.0, 1.0)


def brier_score(y_true: ArrayLike, y_prob: ArrayLike) -> float:
    y, p = _arrays(y_true, y_prob)
    return float(np.mean((p - y) ** 2))


def log_loss(y_true: ArrayLike, y_prob: ArrayLike) -> float:
    y, p = _arrays(y_true, y_prob)
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def calibration_bins(
    y_true: ArrayLike, y_prob: ArrayLike, n_bins: int = 10
) -> list[dict[str, float | int]]:
    """Equal-width reliability bins. Empty bins are omitted."""
    y, p = _arrays(y_true, y_prob)
    indices = np.minimum((p * n_bins).astype(int), n_bins - 1)
    bins: list[dict[str, float | int]] = []
    for b in range(n_bins):
        mask = indices == b
        count = int(mask.sum())
        if count == 0:
            continue
        bins.append(
            {
                "bin_lower": b / n_bins,
                "bin_upper": (b + 1) / n_bins,
                "count": count,
                "mean_predicted": float(p[mask].mean()),
                "observed_rate": float(y[mask].mean()),
            }
        )
    return bins


def expected_calibration_error(y_true: ArrayLike, y_prob: ArrayLike, n_bins: int = 10) -> float:
    total = int(np.asarray(y_true).size)
    if total == 0:
        return float("nan")
    return float(
        sum(
            b["count"] / total * abs(b["mean_predicted"] - b["observed_rate"])
            for b in calibration_bins(y_true, y_prob, n_bins)
        )
    )


def binary_report(y_true: ArrayLike, y_prob: ArrayLike, threshold: float = 0.6) -> dict[str, Any]:
    """Probability metrics. Ranking metrics are ``None`` when only one class is present."""
    y, p = _arrays(y_true, y_prob)
    if len(y) == 0:
        return {"n": 0}
    both = len(np.unique(y)) == 2
    predicted = p >= threshold
    tp = float(np.sum(predicted & (y == 1)))
    report: dict[str, Any] = {
        "n": len(y),
        "positive_rate": float(y.mean()),
        "brier": brier_score(y, p),
        "log_loss": log_loss(y, p),
        "ece": expected_calibration_error(y, p),
        "roc_auc": float(roc_auc_score(y, p)) if both else None,
        "pr_auc": float(average_precision_score(y, p)) if both else None,
        "threshold": threshold,
        "precision_at_threshold": tp / float(predicted.sum()) if predicted.sum() else None,
        "recall_at_threshold": tp / float((y == 1).sum()) if (y == 1).sum() else None,
    }
    return report


def tier_report(
    true_tiers: Sequence[str],
    true_discounts: Sequence[float],
    prob_matrix: np.ndarray,
    coverage: float = 0.8,
) -> dict[str, Any]:
    """Metrics for discount-tier distributions (rows of ``prob_matrix`` follow BUCKET_NAMES)."""
    if len(true_tiers) == 0:
        return {"n": 0}
    probs = np.asarray(prob_matrix, dtype=float)
    probs = probs / probs.sum(axis=1, keepdims=True)
    true_idx = np.array([BUCKET_NAMES.index(t) for t in true_tiers])
    pred_idx = probs.argmax(axis=1)
    actual = np.asarray(true_discounts, dtype=float)
    mids = np.array([(lo + hi - 1) / 2 for _, lo, hi in BUCKETS])
    expected = probs @ mids

    lowers = np.array([lo for _, lo, _ in BUCKETS], dtype=float)
    widths = np.array([hi - 1 - lo for _, lo, hi in BUCKETS], dtype=float)
    cumulative = np.cumsum(probs, axis=1)

    def quantile(q: float) -> np.ndarray:
        idx = np.minimum((cumulative < q).sum(axis=1), len(BUCKETS) - 1)
        rows = np.arange(len(probs))
        before = np.where(idx > 0, cumulative[rows, np.maximum(idx - 1, 0)], 0.0)
        mass = np.maximum(probs[rows, idx], 1e-12)
        return lowers[idx] + widths[idx] * np.clip((q - before) / mass, 0.0, 1.0)

    tail = (1 - coverage) / 2
    q_low, q_mid, q_high = quantile(tail), quantile(0.5), quantile(1 - tail)

    def pinball(q: float, predicted: np.ndarray) -> float:
        diff = actual - predicted
        return float(np.mean(np.maximum(q * diff, (q - 1) * diff)))

    labels = list(range(len(BUCKET_NAMES)))
    per_class = f1_score(true_idx, pred_idx, labels=labels, average=None, zero_division=0)
    present = sorted(set(true_idx.tolist()))
    return {
        "n": len(true_idx),
        "accuracy": float(np.mean(pred_idx == true_idx)),
        "within_one_bucket_accuracy": float(np.mean(np.abs(pred_idx - true_idx) <= 1)),
        "macro_f1": float(np.mean([per_class[i] for i in present])),
        "class_f1": {BUCKET_NAMES[i]: float(per_class[i]) for i in present},
        "discount_mae": float(np.mean(np.abs(expected - actual))),
        # Price error as a fraction of the regular price (currency-independent).
        "price_mae_fraction_of_regular": float(np.mean(np.abs(expected - actual)) / 100.0),
        "quantile_loss": {
            "q10": pinball(tail, q_low),
            "q50": pinball(0.5, q_mid),
            "q90": pinball(1 - tail, q_high),
        },
        "interval_coverage": float(np.mean((actual >= q_low - 0.5) & (actual <= q_high + 0.5))),
        "interval_nominal_coverage": coverage,
    }


def policy_report(
    actions: Sequence[str],
    future_min_ratio: Sequence[float],
    confidence: Sequence[float],
    n_sales: Sequence[float],
    max_wait_days: int,
    material_drop: float = 0.10,
) -> dict[str, Any]:
    """Recommendation-policy outcomes.

    ``future_min_ratio`` is the lowest price inside the wait horizon divided by the
    price at decision time. Savings assume a WAIT buyer purchased at that lowest price,
    which is an upper bound on what a real buyer would capture.
    """
    act = np.asarray(actions)
    ratio = np.minimum(np.asarray(future_min_ratio, dtype=float), 1.0)
    conf = np.asarray(confidence, dtype=float)
    sales = np.asarray(n_sales, dtype=float)

    def summarise(mask: np.ndarray) -> dict[str, Any]:
        wait, buy = mask & (act == "WAIT"), mask & (act == "BUY")
        out: dict[str, Any] = {
            "n": int(mask.sum()),
            "counts": {a: int((mask & (act == a)).sum()) for a in ("BUY", "WAIT", "NEUTRAL")},
            "avg_realized_savings_fraction": None,
            "wait_followed_by_lower_price_rate": None,
            "buy_regret_rate": None,
            "avg_buy_regret_fraction": None,
        }
        if mask.sum():
            # Savings relative to buying immediately: WAIT captures the drop, others 0.
            realized = np.where(act == "WAIT", 1.0 - ratio, 0.0)[mask]
            out["avg_realized_savings_fraction"] = float(realized.mean())
        if wait.sum():
            out["wait_followed_by_lower_price_rate"] = float((ratio[wait] < 1.0).mean())
            out["avg_wait_savings_fraction"] = float((1.0 - ratio[wait]).mean())
        if buy.sum():
            regret = 1.0 - ratio[buy]
            out["buy_regret_rate"] = float((regret >= material_drop).mean())
            out["avg_buy_regret_fraction"] = float(regret.mean())
        return out

    everything = np.ones(len(act), dtype=bool)
    return {
        "max_wait_days": max_wait_days,
        "material_drop_fraction": material_drop,
        "overall": summarise(everything),
        "by_confidence_band": {
            "low": summarise(conf < 0.4),
            "medium": summarise((conf >= 0.4) & (conf < 0.7)),
            "high": summarise(conf >= 0.7),
        },
        "by_history_cohort": {
            "sparse_lt_4_sales": summarise(sales < 4),
            "rich_ge_4_sales": summarise(sales >= 4),
        },
    }
