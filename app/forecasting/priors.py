"""Hierarchical cold-start priors: publisher -> tag cohort -> global -> documented default."""

from __future__ import annotations

import itertools
import statistics
from collections import Counter
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.forecasting.buckets import BUCKET_NAMES, bucket_for
from app.forecasting.calendar import windows_between
from app.forecasting.types import CohortPrior

MIN_COHORT_EVENTS = 8
MIN_COHORT_INTERVALS = 4

# Documented assumption used only when the database holds too little data to estimate a
# cohort: a sale roughly every 75 days, mostly mid-depth, with moderate seasonal uptake.
GLOBAL_DEFAULT_PRIOR = CohortPrior(
    level="GLOBAL_DEFAULT",
    median_interval_days=75.0,
    n_events=0,
    tier_probs={
        "LT_20": 0.10,
        "20_TO_29": 0.15,
        "30_TO_39": 0.17,
        "40_TO_49": 0.13,
        "50_TO_59": 0.20,
        "60_TO_74": 0.15,
        "75_PLUS": 0.10,
    },
    seasonal_participation=0.6,
)

# One sale: (start, max discount %).
CohortEvents = Mapping[Any, Sequence[tuple[datetime, int]]]


def prior_from_events(level: str, events_by_game: CohortEvents) -> CohortPrior | None:
    """Estimate a prior from per-game sale lists, or ``None`` if the cohort is too thin."""
    intervals: list[float] = []
    discounts: list[int] = []
    hits = seen = 0
    for events in events_by_game.values():
        ordered = sorted(events)
        discounts.extend(d for _, d in ordered)
        starts = [s for s, _ in ordered]
        intervals.extend((b - a).total_seconds() / 86400 for a, b in itertools.pairwise(starts))
        if len(starts) >= 2:
            start_days = [s.date() for s in starts]
            for window in windows_between(start_days[0], start_days[-1]):
                if window.start < start_days[0] or window.end > start_days[-1]:
                    continue
                seen += 1
                lo, hi = window.start - timedelta(days=2), window.end + timedelta(days=2)
                hits += any(lo <= day <= hi for day in start_days)
    if len(discounts) < MIN_COHORT_EVENTS or len(intervals) < MIN_COHORT_INTERVALS:
        return None
    counts = Counter(bucket_for(d) for d in discounts)
    total = sum(counts.values())
    # Light smoothing toward the default so no tier is ever impossible.
    tier_probs = {
        name: (counts.get(name, 0) + GLOBAL_DEFAULT_PRIOR.tier_probs[name]) / (total + 1.0)
        for name in BUCKET_NAMES
    }
    participation = (
        (hits + 2 * GLOBAL_DEFAULT_PRIOR.seasonal_participation) / (seen + 2)
        if seen
        else GLOBAL_DEFAULT_PRIOR.seasonal_participation
    )
    return CohortPrior(
        level=level,
        median_interval_days=max(7.0, float(statistics.median(intervals))),
        n_events=len(discounts),
        tier_probs=tier_probs,
        seasonal_participation=participation,
    )


def choose_prior(
    publisher: CohortEvents | None, tag: CohortEvents | None, global_: CohortEvents | None
) -> CohortPrior:
    for level, cohort in (("PUBLISHER", publisher), ("TAG", tag), ("GLOBAL", global_)):
        if cohort:
            prior = prior_from_events(level, cohort)
            if prior is not None:
                return prior
    return GLOBAL_DEFAULT_PRIOR


@dataclass(frozen=True)
class GameEvents:
    game_key: Hashable
    publisher: str | None
    primary_tag: str | None
    events: tuple[tuple[datetime, int], ...]


class PriorIndex:
    """As-of cohort priors for dataset building.

    Priors for a cutoff use only sales that *started before the first day of the
    cutoff's month*, which is a subset of what was knowable at the cutoff. That keeps
    them leakage-safe while making them cacheable per cohort and month.
    """

    def __init__(self, games: Sequence[GameEvents]) -> None:
        self._games = list(games)
        self._by_publisher: dict[str, list[GameEvents]] = {}
        self._by_tag: dict[str, list[GameEvents]] = {}
        for game in self._games:
            if game.publisher:
                self._by_publisher.setdefault(game.publisher, []).append(game)
            if game.primary_tag:
                self._by_tag.setdefault(game.primary_tag, []).append(game)
        self._cache: dict[tuple[str, str | None, datetime], CohortPrior | None] = {}

    @staticmethod
    def _as_of(cutoff: datetime) -> datetime:
        return cutoff.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def _cohort(
        self, level: str, key: str | None, games: Sequence[GameEvents], as_of: datetime
    ) -> CohortPrior | None:
        cache_key = (level, key, as_of)
        if cache_key not in self._cache:
            visible = {g.game_key: [e for e in g.events if e[0] < as_of] for g in games}
            self._cache[cache_key] = prior_from_events(
                level, {k: v for k, v in visible.items() if v}
            )
        return self._cache[cache_key]

    def prior_for(
        self, publisher: str | None, primary_tag: str | None, cutoff: datetime
    ) -> CohortPrior:
        as_of = self._as_of(cutoff)
        if publisher and publisher in self._by_publisher:
            prior = self._cohort("PUBLISHER", publisher, self._by_publisher[publisher], as_of)
            if prior is not None:
                return prior
        if primary_tag and primary_tag in self._by_tag:
            prior = self._cohort("TAG", primary_tag, self._by_tag[primary_tag], as_of)
            if prior is not None:
                return prior
        return self._cohort("GLOBAL", None, self._games, as_of) or GLOBAL_DEFAULT_PRIOR
