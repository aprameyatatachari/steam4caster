"""Leakage-safe training/evaluation dataset construction.

Each row is one (series, cutoff) pair. Features see only observations at or before the
cutoff; labels are computed from what happened strictly after it. A label is left
missing when its horizon extends past ``as_of`` (the future is not yet known).
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from app.forecasting.baseline import baseline_new_sale_probs, baseline_tier_probs
from app.forecasting.buckets import BUCKET_NAMES, bucket_for
from app.forecasting.confidence import score_confidence
from app.forecasting.features.builder import FEATURE_NAMES, build_state
from app.forecasting.priors import GameEvents, PriorIndex
from app.forecasting.sale_events import dedupe_points, derive_sale_events
from app.forecasting.types import HORIZONS, GameContext, PricePoint

TIER_LOOKAHEAD_DAYS = 180
TIER_MIN_SALE_AGE_DAYS = 14
MIN_HISTORY_DAYS = 30


@dataclass(frozen=True)
class SeriesData:
    series_key: str
    game_key: Hashable
    points: Sequence[PricePoint]
    context: GameContext


def prior_index_for(series: Sequence[SeriesData]) -> PriorIndex:
    return PriorIndex(
        [
            GameEvents(
                game_key=s.series_key,
                publisher=s.context.publisher,
                primary_tag=s.context.primary_tag,
                events=tuple(
                    (e.started_at, e.max_discount_pct) for e in derive_sale_events(s.points)
                ),
            )
            for s in series
        ]
    )


def build_rows_for_series(
    series: SeriesData, prior_index: PriorIndex, as_of: datetime, step_days: int = 14
) -> list[dict[str, Any]]:
    points = dedupe_points(series.points)
    if not points:
        return []
    all_events = derive_sale_events(points)
    rows: list[dict[str, Any]] = []
    cutoff = (points[0].at + timedelta(days=MIN_HISTORY_DAYS)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    while cutoff <= as_of - timedelta(days=min(HORIZONS)):
        prior = prior_index.prior_for(series.context.publisher, series.context.primary_tag, cutoff)
        state = build_state(points, series.context, cutoff, prior)
        if state.current is None or state.on_sale or state.current.price_minor <= 0:
            cutoff += timedelta(days=step_days)
            continue
        future_starts = [e for e in all_events if e.started_at > cutoff]
        row: dict[str, Any] = {
            "series_key": series.series_key,
            "cutoff": cutoff,
            **{name: state.features[name] for name in FEATURE_NAMES},
        }
        for horizon in HORIZONS:
            end = cutoff + timedelta(days=horizon)
            known = end <= as_of
            row[f"y_{horizon}"] = (
                float(any(e.started_at <= end for e in future_starts)) if known else math.nan
            )
            later = [p.price_minor for p in points if cutoff < p.at <= end]
            row[f"min_ratio_{horizon}"] = (
                min([*later, state.current.price_minor]) / state.current.price_minor
                if known
                else math.nan
            )
        next_tier: str | None = None
        next_discount = math.nan
        upcoming = next(
            (
                e
                for e in future_starts
                if e.started_at <= cutoff + timedelta(days=TIER_LOOKAHEAD_DAYS)
            ),
            None,
        )
        if upcoming is not None and (
            upcoming.ended_at is not None
            or as_of - upcoming.started_at >= timedelta(days=TIER_MIN_SALE_AGE_DAYS)
        ):
            next_tier = bucket_for(upcoming.max_discount_pct)
            next_discount = float(upcoming.max_discount_pct)
        row["next_tier"] = next_tier
        row["next_discount"] = next_discount

        base_probs = baseline_new_sale_probs(state)
        base_tiers = baseline_tier_probs(state)
        for horizon in HORIZONS:
            row[f"base_p_{horizon}"] = base_probs[horizon]
        for name in BUCKET_NAMES:
            row[f"base_tier_{name}"] = base_tiers[name]
        row["base_confidence"] = score_confidence(state, base_tiers).score
        rows.append(row)
        cutoff += timedelta(days=step_days)
    return rows


def build_dataset(
    series: Sequence[SeriesData], as_of: datetime, step_days: int = 14
) -> pd.DataFrame:
    """Build the full dataset. Returns an empty frame with the right columns if no rows."""
    index = prior_index_for(series)
    rows: list[dict[str, Any]] = []
    for item in series:
        rows.extend(build_rows_for_series(item, index, as_of, step_days))
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame[list(FEATURE_NAMES)] = frame[list(FEATURE_NAMES)].astype(float)
    return frame.sort_values(["cutoff", "series_key"]).reset_index(drop=True)
