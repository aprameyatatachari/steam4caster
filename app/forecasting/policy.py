"""Versioned BUY / WAIT / NEUTRAL recommendation policy.

A pure function of the current price, a forecast, its confidence and the user's
maximum wait. Every threshold is explicit and returned with the decision so a
recommendation can always be reproduced and audited. Outputs are estimates, not
guarantees and not financial advice.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass

from app.core.money import format_minor
from app.forecasting.buckets import expected_discount
from app.models.enums import RecommendationAction

RULESET_VERSION = "rules-1"
ESTIMATE_DISCLAIMER = "This is a statistical estimate, not a guarantee or financial advice."
_P_CAP = 0.97


@dataclass(frozen=True)
class PolicyConfig:
    # Cost of waiting, as a fraction of the current price per 30 days of waiting. This is
    # a stated assumption about impatience, not an objectively known quantity.
    waiting_cost_fraction: float = 0.05
    near_low_tolerance: float = 0.05
    wait_min_probability: float = 0.60
    wait_min_savings_fraction: float = 0.10
    buy_max_savings_fraction: float = 0.03
    min_confidence: float = 0.35
    default_max_wait_days: int = 30


@dataclass(frozen=True)
class PolicyInput:
    current_price_minor: int | None
    regular_price_minor: int | None
    historical_low_minor: int | None
    current_discount_pct: int
    currently_on_sale: bool
    new_sale_probs: Mapping[int, float]
    tier_probs: Mapping[str, float]
    confidence: float
    currency: str | None
    max_wait_days: int | None = None


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    score: float
    sale_probability: float
    max_wait_days: int
    selected_horizon_days: int
    expected_future_price_minor: int | None
    expected_savings_minor: int | None
    waiting_cost_minor: int | None
    reason_codes: list[str]
    summary: str
    thresholds: dict[str, float | int | str]


def probability_within(probs: Mapping[int, float], days: int) -> float:
    """Interpolate P(sale starts within ``days``) assuming a constant hazard between the
    forecast horizons, and the last segment's hazard beyond the longest horizon."""
    anchors = [(0, 0.0)] + [(h, min(_P_CAP, max(0.0, probs[h]))) for h in sorted(probs)]
    if days <= 0:
        return 0.0
    for (d0, p0), (d1, p1) in itertools.pairwise(anchors):
        if days <= d1:
            s0, s1 = 1.0 - p0, max(1e-9, 1.0 - p1)
            hazard = math.log(s0 / s1) / (d1 - d0) if s1 < s0 else 0.0
            return 1.0 - s0 * math.exp(-hazard * (days - d0))
    (d0, p0), (d1, p1) = anchors[-2], anchors[-1]
    s0, s1 = 1.0 - p0, max(1e-9, 1.0 - p1)
    hazard = math.log(s0 / s1) / (d1 - d0) if s1 < s0 else 0.0
    return min(_P_CAP, 1.0 - s1 * math.exp(-hazard * (days - d1)))


def selected_horizon(probs: Mapping[int, float], days: int) -> int:
    """The forecast horizon whose probability anchors the decision for ``days``."""
    horizons = sorted(probs)
    eligible = [h for h in horizons if h <= days]
    return eligible[-1] if eligible else horizons[0]


def _money(amount_minor: int, currency: str | None) -> str:
    return f"{currency} {format_minor(amount_minor, currency)}" if currency else str(amount_minor)


def decide(inp: PolicyInput, config: PolicyConfig | None = None) -> PolicyDecision:
    cfg = config or PolicyConfig()
    max_wait = inp.max_wait_days or cfg.default_max_wait_days
    p_sale = probability_within(inp.new_sale_probs, max_wait)
    horizon = selected_horizon(inp.new_sale_probs, max_wait)
    thresholds: dict[str, float | int | str] = {**asdict(cfg), "ruleset_version": RULESET_VERSION}

    def result(
        action: str,
        score: float,
        reasons: list[str],
        summary: str,
        future: int | None = None,
        savings: int | None = None,
        cost: int | None = None,
    ) -> PolicyDecision:
        return PolicyDecision(
            action=action,
            score=round(min(1.0, max(0.0, score)), 4),
            sale_probability=round(p_sale, 4),
            max_wait_days=max_wait,
            selected_horizon_days=horizon,
            expected_future_price_minor=future,
            expected_savings_minor=savings,
            waiting_cost_minor=cost,
            reason_codes=reasons,
            summary=f"{summary} {ESTIMATE_DISCLAIMER}",
            thresholds=thresholds,
        )

    price, regular = inp.current_price_minor, inp.regular_price_minor
    if price is None or regular is None:
        return result(
            RecommendationAction.NEUTRAL, 0.0, ["NO_PRICE_DATA"],
            "No current price is available for this region, so no recommendation can be made.",
        )  # fmt: skip
    if price == 0:
        return result(
            RecommendationAction.BUY, 1.0, ["FREE"], "The game is currently free.", price, 0, 0
        )

    pct = f"{round(p_sale * 100)}%"
    expected_sale_price = round(regular * (1.0 - expected_discount(inp.tier_probs) / 100.0))
    # If no new sale starts, a buyer who waited pays the regular price when a sale is
    # running now (it is assumed to end), otherwise today's price.
    no_sale_price = regular if inp.currently_on_sale else price
    future = round(p_sale * min(expected_sale_price, no_sale_price) + (1 - p_sale) * no_sale_price)
    savings = price - future
    cost = round(price * cfg.waiting_cost_fraction * max_wait / 30.0)
    utility = savings - cost
    savings_fraction = savings / price

    low = inp.historical_low_minor
    if (
        low is not None
        and inp.current_discount_pct > 0
        and price <= low * (1.0 + cfg.near_low_tolerance)
    ):
        reasons = ["NEAR_HISTORICAL_LOW"] + (["ON_SALE_NOW"] if inp.currently_on_sale else [])
        return result(
            RecommendationAction.BUY, 0.6 + 0.4 * inp.confidence, reasons,
            f"The current price of {_money(price, inp.currency)} is at or near the lowest "
            "recorded price in this region.",
            future, savings, cost,
        )  # fmt: skip

    if inp.confidence < cfg.min_confidence:
        return result(
            RecommendationAction.NEUTRAL, inp.confidence, ["LOW_CONFIDENCE"],
            "There is not enough reliable history to estimate whether waiting is likely to "
            "pay off.",
            future, savings, cost,
        )  # fmt: skip

    if (
        p_sale >= cfg.wait_min_probability
        and savings_fraction >= cfg.wait_min_savings_fraction
        and utility > 0
    ):
        reasons = ["HIGH_SALE_PROBABILITY", "MEANINGFUL_EXPECTED_SAVINGS"]
        if inp.currently_on_sale:
            reasons.append("DEEPER_SALE_LIKELY")
        return result(
            RecommendationAction.WAIT,
            inp.confidence * min(1.0, 0.5 + savings_fraction),
            reasons,
            f"There is an estimated {pct} chance of a new sale starting within {max_wait} "
            f"days, with an expected saving of about {_money(savings, inp.currency)}.",
            future, savings, cost,
        )  # fmt: skip

    if savings_fraction <= cfg.buy_max_savings_fraction:
        reasons = ["SMALL_EXPECTED_SAVINGS"]
        if inp.currently_on_sale:
            reasons.append("ON_SALE_NOW")
        elif p_sale < 0.3:
            reasons.append("LOW_SALE_PROBABILITY")
        detail = (
            "the current sale price is unlikely to be beaten"
            if inp.currently_on_sale
            else f"the estimated chance of a new sale is {pct}"
        )
        return result(
            RecommendationAction.BUY,
            inp.confidence
            * (1.0 - 0.5 * max(0.0, savings_fraction) / max(cfg.buy_max_savings_fraction, 1e-9)),
            reasons,
            f"Waiting up to {max_wait} days is expected to save little: {detail}.",
            future, savings, cost,
        )  # fmt: skip

    reasons = ["MARGINAL_BENEFIT"]
    if utility <= 0 < savings:
        reasons.append("WAITING_COST_EXCEEDS_SAVINGS")
    if p_sale >= cfg.wait_min_probability or savings_fraction >= cfg.wait_min_savings_fraction:
        reasons.append("CONFLICTING_EVIDENCE")
    return result(
        RecommendationAction.NEUTRAL, inp.confidence * 0.5, reasons,
        f"There is an estimated {pct} chance of a new sale within {max_wait} days, but the "
        "expected benefit of waiting is marginal.",
        future, savings, cost,
    )  # fmt: skip
