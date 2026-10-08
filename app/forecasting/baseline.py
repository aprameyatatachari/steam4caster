"""Deterministic baseline forecaster.

Used whenever no compatible ML artifact is active or the series has too little
history. Everything here is a transparent rule over the series state; nothing is
trained. The output is a probability estimate, never a promised date or price.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import date, timedelta

from app.forecasting.buckets import BUCKET_NAMES, bucket_for, normalize
from app.forecasting.calendar import windows_between
from app.forecasting.types import HORIZONS, SeriesState

PRIOR_STRENGTH = 3.0  # pseudo-observations given to the cohort prior
TIER_PRIOR_STRENGTH = 2.0
TIER_RECENCY_DECAY = 0.8
P_MIN, P_MAX = 0.01, 0.97
WINDOW_MAX_CV = 0.5
WINDOW_MIN_INTERVALS = 2
SEASONAL_WINDOW_MIN_PARTICIPATION = 0.7
SEASONAL_WINDOW_MAX_LEAD_DAYS = 60


def _exp_prob(median_interval: float, horizon: float) -> float:
    """P(event within horizon) for a memoryless process with the given median gap."""
    return 1.0 - math.exp(-math.log(2) * horizon / max(median_interval, 1.0))


def cadence_probability(
    intervals: Sequence[float], elapsed_days: float, horizon: int, prior_median: float
) -> float:
    """P(next sale starts within ``horizon`` days | ``elapsed_days`` since the last start).

    Empirical conditional survival over the game's own gaps, shrunk toward the cohort
    prior in proportion to how few gaps remain informative.
    """
    p_prior = _exp_prob(prior_median, horizon)
    if not intervals:
        return p_prior
    surviving = [i for i in intervals if i > elapsed_days]
    if surviving:
        p_empirical = sum(1 for i in surviving if i <= elapsed_days + horizon) / len(surviving)
        n_effective = float(len(surviving))
    else:
        # Overdue relative to every past gap: assume an elevated but uncertain hazard.
        p_empirical = _exp_prob(statistics.median(intervals) / 2, horizon)
        n_effective = 1.0
    weight = n_effective / (n_effective + PRIOR_STRENGTH)
    return weight * p_empirical + (1 - weight) * p_prior


def participation_estimate(state: SeriesState) -> float:
    """Smoothed share of past seasonal sales this title took part in."""
    strength = 2.0
    return (state.seasonal_hits + strength * state.prior.seasonal_participation) / (
        state.seasonal_seen + strength
    )


def seasonal_probability(state: SeriesState, horizon: int) -> float:
    """P(a seasonal sale that this title joins starts inside the horizon)."""
    today = state.cutoff.date()
    upcoming = [
        w
        for w in windows_between(today, today + timedelta(days=horizon))
        if today < w.start <= today + timedelta(days=horizon)
    ]
    if not upcoming:
        return 0.0
    participation = participation_estimate(state)
    return 1.0 - (1.0 - participation) ** len(upcoming)


def baseline_new_sale_probs(state: SeriesState) -> dict[int, float]:
    never_discounted = not state.events
    elapsed = (
        state.elapsed_since_last_start
        if state.elapsed_since_last_start is not None
        else state.history_days
    )
    prior_median = state.prior.median_interval_days
    probs: dict[int, float] = {}
    for horizon in HORIZONS:
        p_cadence = cadence_probability(state.intervals, elapsed, horizon, prior_median)
        p_season = seasonal_probability(state, horizon)
        if never_discounted and state.history_days > 2 * prior_median:
            # A long history without any discount is evidence against the cohort prior.
            shrink = max(0.1, prior_median / state.history_days)
            p_cadence *= shrink
            p_season *= shrink
        # Cadence gaps already contain past seasonal sales, so the seasonal signal is
        # only allowed to add half of its independent contribution.
        combined = 1.0 - (1.0 - p_cadence) * (1.0 - 0.5 * p_season)
        probs[horizon] = max(combined, 0.9 * p_season)
    return enforce_monotonic(probs)


def enforce_monotonic(probs: dict[int, float]) -> dict[int, float]:
    result: dict[int, float] = {}
    floor = 0.0
    for horizon in sorted(probs):
        value = min(P_MAX, max(P_MIN, probs[horizon], floor))
        result[horizon] = value
        floor = value
    return result


def baseline_tier_probs(state: SeriesState) -> dict[str, float]:
    """Recency-weighted history of the game's own sale depths, smoothed by the prior."""
    weights = dict.fromkeys(BUCKET_NAMES, 0.0)
    for age, discount in enumerate(reversed(state.discounts)):
        weights[bucket_for(discount)] += TIER_RECENCY_DECAY**age
    prior = normalize(state.prior.tier_probs)
    return normalize(
        {name: weights[name] + TIER_PRIOR_STRENGTH * prior[name] for name in BUCKET_NAMES}
    )


def derive_window(state: SeriesState, new_sale_probs: dict[int, float]) -> tuple[date, date] | None:
    """A likely sale window, only when the evidence is concentrated enough to give one."""
    if state.on_sale:
        return None
    today = state.cutoff.date()
    upcoming = state.next_seasonal
    lead = (upcoming.start - today).days
    if (
        state.seasonal_seen >= 2
        and participation_estimate(state) >= SEASONAL_WINDOW_MIN_PARTICIPATION
        and lead <= SEASONAL_WINDOW_MAX_LEAD_DAYS
    ):
        return upcoming.start, upcoming.end
    cv = state.features.get("interval_cv")
    if (
        len(state.intervals) >= WINDOW_MIN_INTERVALS
        and cv is not None
        and cv <= WINDOW_MAX_CV
        and state.elapsed_since_last_start is not None
        and new_sale_probs.get(90, 0.0) >= 0.5
    ):
        median = statistics.median(state.intervals)
        spread = statistics.pstdev(state.intervals) if len(state.intervals) > 1 else median * 0.25
        half_width = max(5.0, min(spread, median * 0.5))
        center = state.events[-1].started_at.date() + timedelta(days=round(median))
        if center <= today:  # overdue: the window starts now
            center = today + timedelta(days=round(half_width))
        start = max(today + timedelta(days=1), center - timedelta(days=round(half_width)))
        end = max(start + timedelta(days=3), center + timedelta(days=round(half_width)))
        if (end - start).days <= 45:
            return start, end
    return None
