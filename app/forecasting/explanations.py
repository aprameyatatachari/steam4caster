"""Templated explanation factors.

Factors come from explicit rules over the series state, optionally re-weighted by ML
feature attributions (LightGBM contribution values grouped by feature family). No
language model is involved anywhere.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping

from app.forecasting.baseline import participation_estimate
from app.forecasting.buckets import BUCKET_LABELS
from app.forecasting.types import ExplanationFactor, SeriesState

NEAR_LOW_TOLERANCE = 0.05

# Feature family -> explanation code, used to map ML attributions onto templates.
FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "RECENT_SALE_CADENCE": (
        "interval_median", "interval_mean", "interval_std", "interval_cv", "interval_min",
        "interval_max", "elapsed_ratio", "days_since_last_sale_start",
        "days_since_last_sale_end", "n_sales_total", "n_sales_90d", "n_sales_180d",
        "n_sales_365d", "mean_sale_duration_days",
    ),
    "UPCOMING_SEASONAL_SALE": (
        "days_until_next_seasonal", "next_seasonal_kind", "in_seasonal_window",
        "seasonal_participation_rate", "seasonal_windows_seen", "month", "week_of_year",
        "doy_sin", "doy_cos",
    ),
    "TYPICAL_DISCOUNT_DEPTH": (
        "last_discount", "median_discount", "max_discount", "mean_discount",
        "last3_mean_discount", "discount_std",
    ),
    "COHORT_PRIOR": ("prior_median_interval", "prior_n_events", "prior_seasonal_participation"),
    "PRICE_POSITION": (
        "current_discount", "price_to_regular", "price_to_low", "regular_changes_365d",
        "days_since_regular_change",
    ),
    "GAME_PROFILE": ("game_age_days", "release_month", "is_dlc", "history_days", "n_observations"),
}  # fmt: skip

_PRIOR_LABEL = {
    "PUBLISHER": "other games from the same publisher",
    "TAG": "similar games in the same category",
    "GLOBAL": "the overall catalogue",
    "GLOBAL_DEFAULT": "a general default assumption",
}


def _raw_factors(
    state: SeriesState, new_sale_probs: Mapping[int, float], tier_probs: Mapping[str, float]
) -> list[tuple[str, str, float, str]]:
    """(code, direction, raw weight, text) candidates before normalisation."""
    out: list[tuple[str, str, float, str]] = []
    f = state.features

    if state.on_sale and state.current is not None:
        out.append((
            "CURRENTLY_ON_SALE", "BUY", 0.9,
            f"The game is discounted by {state.current.discount_pct}% right now.",
        ))  # fmt: skip

    ratio = f.get("price_to_low")
    if (
        ratio is not None
        and state.current is not None
        and state.current.discount_pct > 0
        and ratio <= 1.0 + NEAR_LOW_TOLERANCE
    ):
        pct = round(NEAR_LOW_TOLERANCE * 100)
        out.append((
            "NEAR_HISTORICAL_LOW", "BUY", 0.8,
            f"The current price is within {pct}% of the lowest recorded price in this region.",
        ))  # fmt: skip
    if ratio is not None and ratio >= 1.5 and not state.on_sale:
        out.append((
            "ABOVE_HISTORICAL_LOW", "WAIT", 0.3,
            "The current price is well above the lowest recorded price in this region.",
        ))  # fmt: skip

    if len(state.intervals) >= 2:
        lo, hi = round(min(state.intervals)), round(max(state.intervals))
        quartiles = (
            statistics.quantiles(state.intervals, n=4) if len(state.intervals) >= 4 else None
        )
        if quartiles:
            lo, hi = round(quartiles[0]), round(quartiles[2])
        gap = f"every {lo}–{hi} days" if hi > lo else f"about every {lo} days"
        out.append((
            "RECENT_SALE_CADENCE", "WAIT" if new_sale_probs.get(30, 0) >= 0.5 else "NEUTRAL",
            0.6, f"Sales have historically started {gap}.",
        ))  # fmt: skip
        elapsed_ratio = f.get("elapsed_ratio")
        if elapsed_ratio is not None and not state.on_sale:
            days = round(state.elapsed_since_last_start or 0)
            if elapsed_ratio >= 1.0:
                out.append((
                    "OVERDUE_FOR_SALE", "WAIT", 0.5,
                    f"It has been {days} days since the last sale began, longer than the "
                    "typical gap.",
                ))  # fmt: skip
            elif elapsed_ratio <= 0.3:
                out.append((
                    "RECENTLY_ON_SALE", "BUY", 0.4,
                    f"The last sale began only {days} days ago, so another one soon is "
                    "less likely.",
                ))  # fmt: skip

    lead = (state.next_seasonal.start - state.cutoff.date()).days
    participation = participation_estimate(state)
    if not state.on_sale and lead <= 45 and state.seasonal_seen >= 1:
        joined = f"{state.seasonal_hits} of the last {state.seasonal_seen}"
        if participation >= 0.5:
            out.append((
                "UPCOMING_SEASONAL_SALE", "WAIT", 0.3 + 0.5 * participation,
                f"A {state.next_seasonal.label} is estimated to begin in about {lead} days, "
                f"and this game joined {joined} seasonal sales on record.",
            ))  # fmt: skip
        else:
            out.append((
                "SEASONAL_SALE_RARELY_JOINED", "NEUTRAL", 0.2,
                f"A {state.next_seasonal.label} is estimated to begin in about {lead} days, "
                f"but this game joined only {joined} seasonal sales on record.",
            ))  # fmt: skip

    if state.discounts:
        top = max(tier_probs, key=lambda name: tier_probs[name])
        out.append((
            "TYPICAL_DISCOUNT_DEPTH", "NEUTRAL", 0.35,
            f"Past sales most often fell in the {BUCKET_LABELS[top]} discount range "
            f"(most recent: {state.discounts[-1]}%).",
        ))  # fmt: skip

    if not state.events:
        if state.history_days >= 365:
            out.append((
                "NEVER_DISCOUNTED", "BUY", 0.7,
                f"No discount has been recorded in {round(state.history_days)} days of "
                "price history.",
            ))  # fmt: skip
        out.append((
            "INSUFFICIENT_GAME_HISTORY", "NEUTRAL", 0.6,
            "There is too little sale history for this game, so the estimate relies on "
            f"{_PRIOR_LABEL[state.prior.level]}.",
        ))  # fmt: skip
    elif state.n_sales < 3:
        out.append((
            "LIMITED_GAME_HISTORY", "NEUTRAL", 0.4,
            f"Only {state.n_sales} past sale(s) are on record, so the estimate leans on "
            f"{_PRIOR_LABEL[state.prior.level]}.",
        ))  # fmt: skip

    if (f.get("regular_changes_365d") or 0) > 0:
        out.append((
            "REGULAR_PRICE_CHANGED", "NEUTRAL", 0.2,
            "The regular price changed in the last year, which makes past sale prices "
            "less comparable.",
        ))  # fmt: skip
    return out


def build_factors(
    state: SeriesState,
    new_sale_probs: Mapping[int, float],
    tier_probs: Mapping[str, float],
    *,
    ml_group_attributions: Mapping[str, float] | None = None,
    limit: int = 6,
) -> list[ExplanationFactor]:
    """Rule-derived factors, with ML attributions (when given) scaling importance and
    setting direction for the families the model actually relied on."""
    candidates = _raw_factors(state, new_sale_probs, tier_probs)
    code_to_group = {
        "RECENT_SALE_CADENCE": "RECENT_SALE_CADENCE",
        "OVERDUE_FOR_SALE": "RECENT_SALE_CADENCE",
        "RECENTLY_ON_SALE": "RECENT_SALE_CADENCE",
        "UPCOMING_SEASONAL_SALE": "UPCOMING_SEASONAL_SALE",
        "SEASONAL_SALE_RARELY_JOINED": "UPCOMING_SEASONAL_SALE",
        "TYPICAL_DISCOUNT_DEPTH": "TYPICAL_DISCOUNT_DEPTH",
        "INSUFFICIENT_GAME_HISTORY": "COHORT_PRIOR",
        "LIMITED_GAME_HISTORY": "COHORT_PRIOR",
        "REGULAR_PRICE_CHANGED": "PRICE_POSITION",
        "ABOVE_HISTORICAL_LOW": "PRICE_POSITION",
    }
    weighted: list[tuple[str, str, float, str]] = []
    if ml_group_attributions:
        total = sum(abs(v) for v in ml_group_attributions.values()) or 1.0
        for code, direction, weight, text in candidates:
            group = code_to_group.get(code)
            if group is not None and group in ml_group_attributions:
                share = abs(ml_group_attributions[group]) / total
                weight = 0.25 * weight + share
                if direction != "BUY" or code == "RECENTLY_ON_SALE":
                    direction = "WAIT" if ml_group_attributions[group] > 0 else "BUY"
                    if code in ("TYPICAL_DISCOUNT_DEPTH", "REGULAR_PRICE_CHANGED",
                                "INSUFFICIENT_GAME_HISTORY", "LIMITED_GAME_HISTORY"):  # fmt: skip
                        direction = "NEUTRAL"
            weighted.append((code, direction, weight, text))
    else:
        weighted = candidates
    weighted.sort(key=lambda item: item[2], reverse=True)
    weighted = weighted[:limit]
    total_weight = sum(w for _, _, w, _ in weighted) or 1.0
    return [
        ExplanationFactor(
            code=code, direction=direction, importance=weight / total_weight, text=text
        )
        for code, direction, weight, text in weighted
    ]
