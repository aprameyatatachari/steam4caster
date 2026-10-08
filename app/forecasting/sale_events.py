"""Derivation of contiguous sale periods from a price-change log (pure, rerunnable)."""

from __future__ import annotations

from collections.abc import Iterable

from app.forecasting.types import PricePoint, SaleEventData


def dedupe_points(points: Iterable[PricePoint]) -> list[PricePoint]:
    """Sort by time and drop consecutive points that carry no price change."""
    result: list[PricePoint] = []
    for point in sorted(points, key=lambda p: (p.at, p.discount_pct, p.price_minor)):
        if result and (
            result[-1].price_minor == point.price_minor
            and result[-1].regular_minor == point.regular_minor
            and result[-1].discount_pct == point.discount_pct
        ):
            continue
        if result and result[-1].at == point.at:
            result[-1] = point  # same instant: keep the deeper discount (sorted last)
            continue
        result.append(point)
    return result


def derive_sale_events(points: Iterable[PricePoint]) -> list[SaleEventData]:
    """A sale starts at the first discounted observation and ends at the next
    undiscounted one. A sale still running at the end of the log has ``ended_at=None``.
    Depth changes inside a discounted period stay within one event.
    """
    events: list[SaleEventData] = []
    open_start: PricePoint | None = None
    min_price = 0
    max_cut = 0
    for point in dedupe_points(points):
        discounted = point.discount_pct > 0 and point.price_minor < point.regular_minor
        if discounted and open_start is None:
            open_start, min_price, max_cut = point, point.price_minor, point.discount_pct
        elif discounted and open_start is not None:
            min_price = min(min_price, point.price_minor)
            max_cut = max(max_cut, point.discount_pct)
        elif not discounted and open_start is not None:
            events.append(
                SaleEventData(
                    started_at=open_start.at,
                    ended_at=point.at,
                    initial_price_minor=open_start.price_minor,
                    min_price_minor=min_price,
                    regular_minor=open_start.regular_minor,
                    max_discount_pct=max_cut,
                )
            )
            open_start = None
    if open_start is not None:
        events.append(
            SaleEventData(
                started_at=open_start.at,
                ended_at=None,
                initial_price_minor=open_start.price_minor,
                min_price_minor=min_price,
                regular_minor=open_start.regular_minor,
                max_discount_pct=max_cut,
            )
        )
    return events
