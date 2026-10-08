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
    windows: list[SeasonalWindow] = []
    if year >= 2023:  # the Spring Sale was introduced in 2023
        spring = _nth_weekday(year, 3, THURSDAY, 3 if year == 2023 else 2)
        windows.append(SeasonalWindow("SPRING", spring, spring + timedelta(days=7)))
    summer = _last_weekday(year, 6, THURSDAY)
    windows.append(SeasonalWindow("SUMMER", summer, summer + timedelta(days=14)))
    if year >= 2025:  # the Autumn Sale moved to late September/early October in 2025
        autumn = _last_weekday(year, 9, MONDAY)
    else:  # previously the week of US Thanksgiving
        autumn = _nth_weekday(year, 11, THURSDAY, 4) - timedelta(days=2)
    windows.append(SeasonalWindow("AUTUMN", autumn, autumn + timedelta(days=7)))
    winter = _nth_weekday(year, 12, THURSDAY, 3)
    windows.append(SeasonalWindow("WINTER", winter, winter + timedelta(days=14)))
    return tuple(sorted(windows, key=lambda w: w.start))


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
