"""Leakage-safe feature construction.

``build_state`` only ever looks at observations with ``at <= cutoff``. The same function
is used to build training rows and for online inference, so there is a single
definition of every feature.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Sequence
from datetime import datetime, timedelta

from app.forecasting.calendar import active_window, next_window, windows_between
from app.forecasting.sale_events import dedupe_points, derive_sale_events
from app.forecasting.types import CohortPrior, GameContext, PricePoint, SaleEventData, SeriesState

SEASON_PAD_DAYS = 2

FEATURE_NAMES: tuple[str, ...] = (
    "n_sales_total",
    "n_sales_90d",
    "n_sales_180d",
    "n_sales_365d",
    "days_since_last_sale_start",
    "days_since_last_sale_end",
    "interval_median",
    "interval_mean",
    "interval_std",
    "interval_cv",
    "interval_min",
    "interval_max",
    "elapsed_ratio",
    "last_discount",
    "median_discount",
    "max_discount",
    "mean_discount",
    "last3_mean_discount",
    "discount_std",
    "mean_sale_duration_days",
    "history_days",
    "n_observations",
    "game_age_days",
    "release_month",
    "month",
    "week_of_year",
    "doy_sin",
    "doy_cos",
    "days_until_next_seasonal",
    "next_seasonal_kind",
    "in_seasonal_window",
    "seasonal_participation_rate",
    "seasonal_windows_seen",
    "current_discount",
    "price_to_regular",
    "price_to_low",
    "regular_changes_365d",
    "days_since_regular_change",
    "prior_median_interval",
    "prior_n_events",
    "prior_seasonal_participation",
    "is_dlc",
)

_SEASON_CODE = {"SPRING": 0.0, "SUMMER": 1.0, "AUTUMN": 2.0, "WINTER": 3.0}


def _days(delta: timedelta) -> float:
    return delta.total_seconds() / 86400.0


def seasonal_participation(
    events: Sequence[SaleEventData], first_seen: datetime, cutoff: datetime
) -> tuple[int, int]:
    """(hits, seen) over seasonal windows that fully ended between first_seen and cutoff."""
    hits = seen = 0
    pad = timedelta(days=SEASON_PAD_DAYS)
    for window in windows_between(first_seen.date(), cutoff.date()):
        if window.start < first_seen.date() or window.end >= cutoff.date():
            continue
        seen += 1
        for event in events:
            end = (event.ended_at or cutoff).date()
            if event.started_at.date() <= window.end + pad and end >= window.start - pad:
                hits += 1
                break
    return hits, seen


def build_state(
    points: Sequence[PricePoint],
    context: GameContext,
    cutoff: datetime,
    prior: CohortPrior,
    *,
    historical_low_minor: int | None = None,
    data_age_days: float = 0.0,
) -> SeriesState:
    """Summarise a series as of ``cutoff``. Points after the cutoff are ignored.

    ``historical_low_minor`` may add a provider-reported low for the same region and
    currency; it must itself be as-of the cutoff (training passes ``None``).
    """
    visible = dedupe_points(p for p in points if p.at <= cutoff)
    events = derive_sale_events(visible)
    starts = [e.started_at for e in events]
    intervals = [_days(b - a) for a, b in itertools.pairwise(starts)]
    discounts = [e.max_discount_pct for e in events]
    current = visible[-1] if visible else None
    on_sale = bool(events) and events[-1].ended_at is None

    first_seen = visible[0].at if visible else cutoff
    history_days = _days(cutoff - first_seen)
    hits, seen = seasonal_participation(events, first_seen, cutoff)

    observed_low = min((p.price_minor for p in visible), default=None)
    lows = [v for v in (observed_low, historical_low_minor) if v is not None]
    low = min(lows) if lows else None

    upcoming = next_window(cutoff.date())
    active = active_window(cutoff.date())

    f: dict[str, float | None] = dict.fromkeys(FEATURE_NAMES)
    f["n_sales_total"] = float(len(events))
    for days in (90, 180, 365):
        since = cutoff - timedelta(days=days)
        f[f"n_sales_{days}d"] = float(sum(1 for s in starts if s >= since))
    elapsed_start: float | None = None
    if events:
        last = events[-1]
        elapsed_start = _days(cutoff - last.started_at)
        f["days_since_last_sale_start"] = elapsed_start
        f["days_since_last_sale_end"] = (
            0.0 if last.ended_at is None else _days(cutoff - last.ended_at)
        )
        f["last_discount"] = float(discounts[-1])
        f["median_discount"] = float(statistics.median(discounts))
        f["max_discount"] = float(max(discounts))
        f["mean_discount"] = float(statistics.fmean(discounts))
        f["last3_mean_discount"] = float(statistics.fmean(discounts[-3:]))
        f["discount_std"] = float(statistics.pstdev(discounts)) if len(discounts) > 1 else 0.0
        durations = [_days(e.ended_at - e.started_at) for e in events if e.ended_at is not None]
        if durations:
            f["mean_sale_duration_days"] = float(statistics.fmean(durations))
    if intervals:
        median = float(statistics.median(intervals))
        mean = float(statistics.fmean(intervals))
        std = float(statistics.pstdev(intervals)) if len(intervals) > 1 else 0.0
        f["interval_median"], f["interval_mean"], f["interval_std"] = median, mean, std
        f["interval_cv"] = std / mean if mean > 0 else None
        f["interval_min"], f["interval_max"] = float(min(intervals)), float(max(intervals))
        if elapsed_start is not None and median > 0:
            f["elapsed_ratio"] = elapsed_start / median
    f["history_days"] = history_days
    f["n_observations"] = float(len(visible))
    if context.release_date is not None:
        f["game_age_days"] = float(max(0, (cutoff.date() - context.release_date).days))
        f["release_month"] = float(context.release_date.month)
    day_of_year = cutoff.timetuple().tm_yday
    f["month"] = float(cutoff.month)
    f["week_of_year"] = float(cutoff.isocalendar().week)
    f["doy_sin"] = math.sin(2 * math.pi * day_of_year / 365.25)
    f["doy_cos"] = math.cos(2 * math.pi * day_of_year / 365.25)
    f["days_until_next_seasonal"] = float((upcoming.start - cutoff.date()).days)
    f["next_seasonal_kind"] = _SEASON_CODE[upcoming.kind]
    f["in_seasonal_window"] = 1.0 if active else 0.0
    f["seasonal_participation_rate"] = hits / seen if seen else None
    f["seasonal_windows_seen"] = float(seen)
    if current is not None:
        f["current_discount"] = float(current.discount_pct)
        if current.regular_minor > 0:
            f["price_to_regular"] = current.price_minor / current.regular_minor
        if low:
            f["price_to_low"] = current.price_minor / low
        changes = [
            b.at for a, b in itertools.pairwise(visible) if a.regular_minor != b.regular_minor
        ]
        year_ago = cutoff - timedelta(days=365)
        f["regular_changes_365d"] = float(sum(1 for at in changes if at >= year_ago))
        if changes:
            f["days_since_regular_change"] = _days(cutoff - changes[-1])
    f["prior_median_interval"] = prior.median_interval_days
    f["prior_n_events"] = float(prior.n_events)
    f["prior_seasonal_participation"] = prior.seasonal_participation
    f["is_dlc"] = 1.0 if (context.type or "").lower() == "dlc" else 0.0

    return SeriesState(
        cutoff=cutoff,
        points=visible,
        events=events,
        intervals=intervals,
        discounts=discounts,
        on_sale=on_sale,
        current=current,
        historical_low_minor=low,
        history_days=history_days,
        elapsed_since_last_start=elapsed_start,
        seasonal_hits=hits,
        seasonal_seen=seen,
        next_seasonal=upcoming,
        active_seasonal=active,
        prior=prior,
        context=context,
        data_age_days=data_age_days,
        features=f,
    )


def missing_feature_rate(features: dict[str, float | None]) -> float:
    values = [features.get(name) for name in FEATURE_NAMES]
    return sum(1 for v in values if v is None) / len(values)
