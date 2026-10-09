"""Approximate recurring Steam seasonal-sale windows.

Valve does not publish a machine-readable schedule, so these are *estimates* derived
from the historical pattern (documented in ``docs/forecasting.md``). They are used only
as features and as an explainable hint, never as a promise that a sale will occur.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

SEASON_KINDS = ("SPRING", "SUMMER", "AUTUMN", "WINTER")
THURSDAY = 3
MONDAY = 0
CURRENT_PATTERN_YEAR = 2026


@dataclass(frozen=True)
class SeasonalWindow:
    kind: str
    start: date
    end: date

    @property
    def label(self) -> str:
        return f"{self.kind.title()} Sale"


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


@lru_cache(maxsize=64)
def seasonal_windows(year: int) -> tuple[SeasonalWindow, ...]:
    # From CURRENT_PATTERN_YEAR on, the rules follow Steam's usual yearly schedule as
    # supplied by the project owner: Spring one week in mid-to-late March, Summer two
    # weeks from late June, Autumn one week in early October, Winter from mid-December
    # into early January. Earlier years keep the dates that actually happened, because
    # they are matched against real price history.
    current = year >= CURRENT_PATTERN_YEAR
    windows: list[SeasonalWindow] = []
    if year >= 2023:  # the Spring Sale was introduced in 2023
        spring = _nth_weekday(year, 3, THURSDAY, 3 if (current or year == 2023) else 2)
        windows.append(SeasonalWindow("SPRING", spring, spring + timedelta(days=7)))
    summer = _last_weekday(year, 6, THURSDAY)
    windows.append(SeasonalWindow("SUMMER", summer, summer + timedelta(days=14)))
    if current:  # early October
        autumn = _nth_weekday(year, 10, THURSDAY, 1)
    elif year == 2025:  # the year it moved from Thanksgiving week
        autumn = _last_weekday(year, 9, MONDAY)
    else:  # previously the week of US Thanksgiving
        autumn = _nth_weekday(year, 11, THURSDAY, 4) - timedelta(days=2)
    windows.append(SeasonalWindow("AUTUMN", autumn, autumn + timedelta(days=7)))
    winter = _nth_weekday(year, 12, THURSDAY, 3)
    windows.append(SeasonalWindow("WINTER", winter, winter + timedelta(days=18 if current else 14)))
    return tuple(sorted(windows, key=lambda w: w.start))


@lru_cache(maxsize=64)
def next_fests(year: int) -> tuple[SeasonalWindow, ...]:
    """Estimated Steam Next Fest weeks (February/March, June, October).

    Next Fest showcases free demos; it is not a discount event, so it is shown to users
    but never used as a sale signal in forecasts.
    """
    starts = (
        _nth_weekday(year, 2, MONDAY, 4),
        _nth_weekday(year, 6, MONDAY, 2),
        _nth_weekday(year, 10, MONDAY, 2),
    )
    return tuple(SeasonalWindow("NEXT_FEST", s, s + timedelta(days=7)) for s in starts)


def windows_between(start: date, end: date) -> list[SeasonalWindow]:
    """Seasonal windows that overlap ``[start, end]``."""
    result: list[SeasonalWindow] = []
    for year in range(start.year - 1, end.year + 1):
        for window in seasonal_windows(year):
            if window.end >= start and window.start <= end:
                result.append(window)
    return result


def next_window(after: date) -> SeasonalWindow:
    """First seasonal window starting strictly after ``after``."""
    for year in (after.year, after.year + 1):
        for window in seasonal_windows(year):
            if window.start > after:
                return window
    raise AssertionError("unreachable: every year has seasonal windows")


def active_window(on: date) -> SeasonalWindow | None:
    for window in windows_between(on, on):
        if window.start <= on <= window.end:
            return window
    return None
