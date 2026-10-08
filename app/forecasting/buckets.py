"""Discount tiers and conversion of a tier distribution into price quantiles."""

from __future__ import annotations

from collections.abc import Mapping

from app.core.money import apply_discount

# (name, inclusive lower %, exclusive upper %). The outer bounds are modelling
# assumptions: discounts under 5% and over 90% are treated as vanishingly rare.
BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("LT_20", 5, 20),
    ("20_TO_29", 20, 30),
    ("30_TO_39", 30, 40),
    ("40_TO_49", 40, 50),
    ("50_TO_59", 50, 60),
    ("60_TO_74", 60, 75),
    ("75_PLUS", 75, 91),
)
BUCKET_NAMES: tuple[str, ...] = tuple(name for name, _, _ in BUCKETS)
BUCKET_LABELS: dict[str, str] = {
    "LT_20": "under 20%",
    "20_TO_29": "20–29%",
    "30_TO_39": "30–39%",
    "40_TO_49": "40–49%",
    "50_TO_59": "50–59%",
    "60_TO_74": "60–74%",
    "75_PLUS": "75% or more",
}


def bucket_for(discount_pct: float) -> str:
    for name, _, upper in BUCKETS:
        if discount_pct < upper:
            return name
    return BUCKETS[-1][0]


def bucket_index(name: str) -> int:
    return BUCKET_NAMES.index(name)


def normalize(probs: Mapping[str, float]) -> dict[str, float]:
    """Return a full, non-negative distribution over all buckets that sums to 1."""
    cleaned = {name: max(0.0, float(probs.get(name, 0.0))) for name in BUCKET_NAMES}
    total = sum(cleaned.values())
    if total <= 0:
        return {name: 1.0 / len(BUCKET_NAMES) for name in BUCKET_NAMES}
    return {name: value / total for name, value in cleaned.items()}


def expected_discount(probs: Mapping[str, float]) -> float:
    """Expected discount percentage, using each bucket's midpoint."""
    dist = normalize(probs)
    return sum(dist[name] * (lower + upper - 1) / 2 for name, lower, upper in BUCKETS)


def discount_quantile(probs: Mapping[str, float], q: float) -> float:
    """Discount percentage at quantile ``q`` assuming uniform mass inside each bucket."""
    dist = normalize(probs)
    q = min(max(q, 0.0), 1.0)
    cumulative = 0.0
    for name, lower, upper in BUCKETS:
        mass = dist[name]
        if mass > 0 and cumulative + mass >= q:
            return lower + (upper - 1 - lower) * ((q - cumulative) / mass)
        cumulative += mass
    return float(BUCKETS[-1][2] - 1)


def price_interval(
    regular_minor: int, probs: Mapping[str, float], coverage: float = 0.8
) -> tuple[int, int, int]:
    """(lower, median, upper) sale price in minor units for the central ``coverage`` mass.

    A deeper discount means a lower price, so the lower price bound uses the upper
    discount quantile. Results are clamped to ``[0, regular]`` and ordered.
    """
    tail = (1.0 - coverage) / 2
    lower = apply_discount(regular_minor, discount_quantile(probs, 1 - tail) / 100)
    median = apply_discount(regular_minor, discount_quantile(probs, 0.5) / 100)
    upper = apply_discount(regular_minor, discount_quantile(probs, tail) / 100)
    lower, median, upper = sorted((lower, median, upper))
    return lower, median, upper


def entropy_ratio(probs: Mapping[str, float]) -> float:
    """Shannon entropy of the tier distribution as a fraction of the maximum (0..1)."""
    import math

    dist = normalize(probs)
    entropy = -sum(p * math.log(p) for p in dist.values() if p > 0)
    return entropy / math.log(len(BUCKET_NAMES))
