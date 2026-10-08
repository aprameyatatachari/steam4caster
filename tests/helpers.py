"""Test doubles and builders shared across test modules."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from app.forecasting.types import PricePoint
from app.providers.pricing.base import (
    Money,
    ProviderDeal,
    ProviderError,
    ProviderGame,
    ProviderGameInfo,
    ProviderGamePrices,
    ProviderHistoryPoint,
    ProviderShop,
)

STEAM = 61
GAME_ID = "018d937f-0000-7000-8000-000000000001"
NOW = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)  # a date well away from seasonal windows


def price_points(
    now: datetime,
    *,
    sales: Sequence[tuple[int, int, int]],
    regular: int = 2000,
    first_days_ago: int = 800,
) -> list[PricePoint]:
    """Build a change log. ``sales`` are ``(start_days_ago, length_days, discount_pct)``;
    a length of 0 leaves the sale running."""
    points = [PricePoint(now - timedelta(days=first_days_ago), regular, regular, 0)]
    for start, length, cut in sorted(sales, reverse=True):
        begin = now - timedelta(days=start)
        points.append(PricePoint(begin, round(regular * (100 - cut) / 100), regular, cut))
        if length:
            points.append(PricePoint(begin + timedelta(days=length), regular, regular, 0))
    return points


def regular_sales(
    count: int, interval: int, last_days_ago: int, cut: int = 50
) -> list[tuple[int, int, int]]:
    return [(last_days_ago + i * interval, 7, cut) for i in range(count)]


def history_point(
    at: datetime, cut: int, regular: int = 2000, currency: str = "USD"
) -> ProviderHistoryPoint:
    return ProviderHistoryPoint(
        timestamp=at,
        shop_id=STEAM,
        cut=cut,
        price=Money(amount_minor=round(regular * (100 - cut) / 100), currency=currency),
        regular=Money(amount_minor=regular, currency=currency),
    )


def to_history(points: Sequence[PricePoint], currency: str = "USD") -> list[ProviderHistoryPoint]:
    return [history_point(p.at, p.discount_pct, p.regular_minor, currency) for p in points]


class ScriptedProvider:
    """A one-game provider whose history the test controls, with scriptable failures."""

    name = "scripted"

    def __init__(self, title: str = "Scripted Quest", publisher: str = "Script House") -> None:
        self.title = title
        self.publisher = publisher
        self.series: dict[str, list[ProviderHistoryPoint]] = {}
        self.failures: list[ProviderError] = []
        self.calls: dict[str, int] = {}

    def _enter(self, operation: str) -> None:
        self.calls[operation] = self.calls.get(operation, 0) + 1
        if self.failures:
            raise self.failures.pop(0)

    async def aclose(self) -> None:
        return None

    def _game(self) -> ProviderGame:
        return ProviderGame(
            provider_id=GAME_ID, slug="scripted-quest", title=self.title, type="game"
        )

    async def search_games(self, title: str, limit: int = 20) -> list[ProviderGame]:
        self._enter("search")
        return [self._game()] if title.lower() in self.title.lower() else []

    async def lookup_game(
        self, *, title: str | None = None, steam_app_id: int | None = None
    ) -> ProviderGame | None:
        self._enter("lookup")
        return self._game() if steam_app_id == 4242 or title == self.title else None

    async def get_game_info(self, provider_id: str) -> ProviderGameInfo | None:
        self._enter("info")
        if provider_id != GAME_ID:
            return None
        return ProviderGameInfo(
            **self._game().model_dump(),
            steam_app_id=4242,
            release_date=date(2022, 1, 10),
            developers=["Script House"],
            publishers=[self.publisher],
            tags=["RPG"],
            url="https://example.invalid/game/scripted-quest/?affiliate=keep-me",
        )

    async def list_shops(self, country: str) -> list[ProviderShop]:
        self._enter("shops")
        return [ProviderShop(provider_shop_id=STEAM, name="Steam")]

    async def get_price_history(
        self, provider_id: str, country: str, shop_ids: Sequence[int], since: datetime
    ) -> list[ProviderHistoryPoint]:
        self._enter("history")
        return [p for p in self.series.get(country, []) if p.timestamp >= since]

    async def get_current_prices(
        self, provider_ids: Sequence[str], country: str, shop_ids: Sequence[int]
    ) -> list[ProviderGamePrices]:
        self._enter("prices")
        series = self.series.get(country, [])
        if GAME_ID not in provider_ids or not series:
            return [ProviderGamePrices(provider_id=p) for p in provider_ids]
        last = series[-1]
        low = min(p.price.amount_minor for p in series)
        return [
            ProviderGamePrices(
                provider_id=GAME_ID,
                deals=[
                    ProviderDeal(
                        shop_id=STEAM, shop_name="Steam", price=last.price, regular=last.regular,
                        cut=last.cut, timestamp=last.timestamp,
                        store_low=Money(amount_minor=low, currency=last.price.currency),
                        url="https://example.invalid/steam/app/4242/?affiliate=keep-me",
                    )
                ],
            )
        ]  # fmt: skip
