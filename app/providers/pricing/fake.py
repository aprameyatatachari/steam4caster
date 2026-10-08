"""Deterministic fake price provider for tests and explicit local demos.

It is selected only by ``PRICE_PROVIDER=fake`` and refuses to run in production. The
catalogue and price histories are synthetic and are never mixed with real data.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from app.forecasting.calendar import windows_between
from app.providers.pricing.base import (
    Money,
    ProviderDeal,
    ProviderGame,
    ProviderGameInfo,
    ProviderGamePrices,
    ProviderHistoryPoint,
    ProviderShop,
)

STEAM_SHOP_ID = 61
_SHOPS = [ProviderShop(provider_shop_id=STEAM_SHOP_ID, name="Steam"),
          ProviderShop(provider_shop_id=35, name="GOG")]  # fmt: skip

# Synthetic regional price points keyed by a US-style tier. These are independent
# per-region list prices, not currency conversions.
_REGIONS: dict[str, tuple[str, dict[int, int]]] = {
    "US": ("USD", {10: 999, 20: 1999, 30: 2999, 40: 3999, 60: 5999}),
    "IN": ("INR", {10: 34900, 20: 67900, 30: 99900, 40: 129900, 60: 249900}),
    "GB": ("GBP", {10: 849, 20: 1699, 30: 2499, 40: 3499, 60: 4999}),
    "DE": ("EUR", {10: 999, 20: 1999, 30: 2999, 40: 3999, 60: 5999}),
    "JP": ("JPY", {10: 1200, 20: 2300, 30: 3400, 40: 4500, 60: 7900}),
}


@dataclass(frozen=True)
class _FakeGame:
    title: str
    steam_app_id: int
    release: date
    publisher: str
    developer: str
    tags: tuple[str, ...]
    tier: int
    cadence_days: int  # typical gap between the publisher's own promotions (0 = seasonal only)
    depth: int  # typical discount percentage
    seasonal_rate: float
    type: str = "game"

    @property
    def provider_id(self) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"fake-itad:{self.steam_app_id}"))

    @property
    def slug(self) -> str:
        return "-".join("".join(c if c.isalnum() else " " for c in self.title.lower()).split())


_CATALOG: tuple[_FakeGame, ...] = (
    _FakeGame("Starfall Tactics", 900001, date(2019, 3, 14), "Northwind Games", "Northwind Games",
              ("Strategy", "Turn-Based"), 30, 60, 50, 0.95),
    _FakeGame("Starfall Tactics II", 900002, date(2023, 9, 21), "Northwind Games",
              "Northwind Games", ("Strategy", "Turn-Based"), 60, 75, 25, 0.9),
    _FakeGame("Hollow Lantern", 900003, date(2018, 6, 5), "Emberlight", "Emberlight",
              ("Metroidvania", "Indie"), 20, 45, 60, 0.9),
    _FakeGame("Iron Meridian", 900004, date(2020, 11, 12), "Kestrel Interactive", "Forge Nine",
              ("RPG", "Open World"), 60, 90, 50, 0.85),
    _FakeGame("Paper Kingdoms", 900005, date(2021, 2, 2), "Emberlight", "Quill & Co",
              ("City Builder", "Indie"), 20, 50, 35, 0.8),
    _FakeGame("Velocity Drift", 900006, date(2017, 8, 30), "Kestrel Interactive", "Apex Lab",
              ("Racing", "Multiplayer"), 40, 40, 75, 1.0),
    _FakeGame("Quiet Harbor", 900007, date(2022, 5, 19), "Saltmarsh", "Saltmarsh",
              ("Simulation", "Relaxing"), 10, 0, 20, 0.6),
    _FakeGame("Ashen Circuit", 900008, date(2024, 10, 10), "Kestrel Interactive", "Forge Nine",
              ("Action", "Roguelike"), 30, 120, 20, 0.5),
    _FakeGame("Monolith Evergreen", 900009, date(2016, 4, 1), "Stonegate", "Stonegate",
              ("Sandbox", "Survival"), 30, 0, 0, 0.0),
    _FakeGame("Lumen Archive", 900010, date(2021, 7, 8), "Saltmarsh", "Prism Works",
              ("Puzzle", "Indie"), 20, 70, 40, 0.75),
    _FakeGame("Starfall Tactics - Frontier Pack", 900011, date(2020, 5, 7), "Northwind Games",
              "Northwind Games", ("Strategy",), 10, 60, 50, 0.9, "dlc"),
    _FakeGame("Brand New Horizon", 900012, date(2026, 8, 20), "Northwind Games",
              "Northwind Games", ("Adventure",), 40, 0, 0, 0.0),
)  # fmt: skip


def _seed(*parts: object) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _snap_discount(value: float) -> int:
    return min((10, 15, 20, 25, 30, 33, 40, 50, 60, 66, 75, 80, 90), key=lambda d: abs(d - value))


class FakePriceProvider:
    name = "fake"

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self.calls: dict[str, int] = {}
        self._by_id = {g.provider_id: g for g in _CATALOG}

    def _count(self, operation: str) -> None:
        self.calls[operation] = self.calls.get(operation, 0) + 1

    async def aclose(self) -> None:
        return None

    @staticmethod
    def catalog_ids() -> list[str]:
        return [g.provider_id for g in _CATALOG]

    @staticmethod
    def _game(g: _FakeGame) -> ProviderGame:
        return ProviderGame(
            provider_id=g.provider_id, slug=g.slug, title=g.title, type=g.type, mature=False,
            assets={"boxart": f"https://example.invalid/fake/{g.steam_app_id}/boxart.jpg",
                    "banner600": f"https://example.invalid/fake/{g.steam_app_id}/banner600.jpg"},
        )  # fmt: skip

    async def search_games(self, title: str, limit: int = 20) -> list[ProviderGame]:
        self._count("search")
        needle = title.lower().strip()
        return [self._game(g) for g in _CATALOG if needle in g.title.lower()][:limit]

    async def lookup_game(
        self, *, title: str | None = None, steam_app_id: int | None = None
    ) -> ProviderGame | None:
        self._count("lookup")
        for g in _CATALOG:
            if steam_app_id is not None and g.steam_app_id == steam_app_id:
                return self._game(g)
            if title is not None and g.title.lower() == title.lower().strip():
                return self._game(g)
        return None

    async def get_game_info(self, provider_id: str) -> ProviderGameInfo | None:
        self._count("info")
        g = self._by_id.get(provider_id)
        if g is None:
            return None
        return ProviderGameInfo(
            **self._game(g).model_dump(),
            steam_app_id=g.steam_app_id,
            early_access=False,
            release_date=g.release,
            developers=[g.developer],
            publishers=[g.publisher],
            tags=list(g.tags),
            url=f"https://example.invalid/fake/game/{g.slug}/",
        )

    async def list_shops(self, country: str) -> list[ProviderShop]:
        self._count("shops")
        return list(_SHOPS)

    def _series(self, g: _FakeGame, country: str) -> list[ProviderHistoryPoint]:
        """Full synthetic change log for one game/region up to the provider clock."""
        if country not in _REGIONS:
            return []
        currency, tiers = _REGIONS[country]
        regular = tiers[g.tier]
        now = self._clock()
        if g.release > now.date():
            return []
        rng = random.Random(_seed(g.steam_app_id, "series"))  # same sale timing in all regions
        sales: list[tuple[date, date, int]] = []
        for window in windows_between(g.release + timedelta(days=45), now.date()):
            if rng.random() < g.seasonal_rate and g.depth > 0:
                depth = _snap_discount(g.depth + rng.choice((-10, 0, 0, 0, 10)))
                sales.append((window.start, window.end, depth))
        if g.cadence_days and g.depth > 0:
            cursor = g.release + timedelta(days=60 + rng.randint(0, 20))
            while cursor < now.date():
                length = rng.choice((4, 7, 7, 14))
                depth = _snap_discount(g.depth - 10 + rng.choice((-5, 0, 0, 5)))
                sales.append((cursor, cursor + timedelta(days=length), depth))
                cursor += timedelta(days=max(14, round(g.cadence_days * rng.uniform(0.85, 1.2))))
        sales.sort()
        merged: list[tuple[date, date, int]] = []
        for start, end, depth in sales:
            if merged and start <= merged[-1][1] + timedelta(days=5):
                continue  # drop promotions that would collide with the previous one
            merged.append((start, end, depth))

        def at(day: date) -> datetime:
            return datetime.combine(day, time(17, 0), tzinfo=UTC)

        def point(when: datetime, cut: int) -> ProviderHistoryPoint:
            price = round(regular * (100 - cut) / 100)
            return ProviderHistoryPoint(
                timestamp=when, shop_id=STEAM_SHOP_ID, cut=cut,
                price=Money(amount_minor=price, currency=currency),
                regular=Money(amount_minor=regular, currency=currency),
            )  # fmt: skip

        points = [point(at(g.release), 0)]
        for start, end, depth in merged:
            if at(start) > now:
                break
            points.append(point(at(start), depth))
            if at(end) <= now:
                points.append(point(at(end), 0))
        return points

    async def get_price_history(
        self, provider_id: str, country: str, shop_ids: Sequence[int], since: datetime
    ) -> list[ProviderHistoryPoint]:
        self._count("history")
        g = self._by_id.get(provider_id)
        if g is None or (shop_ids and STEAM_SHOP_ID not in shop_ids):
            return []
        return [p for p in self._series(g, country) if p.timestamp >= since]

    async def get_current_prices(
        self, provider_ids: Sequence[str], country: str, shop_ids: Sequence[int]
    ) -> list[ProviderGamePrices]:
        self._count("prices")
        results: list[ProviderGamePrices] = []
        for provider_id in provider_ids:
            g = self._by_id.get(provider_id)
            series = self._series(g, country) if g else []
            if g is None or not series or (shop_ids and STEAM_SHOP_ID not in shop_ids):
                results.append(ProviderGamePrices(provider_id=provider_id))
                continue
            last = series[-1]
            low = min(p.price.amount_minor for p in series)
            results.append(
                ProviderGamePrices(
                    provider_id=provider_id,
                    deals=[
                        ProviderDeal(
                            shop_id=STEAM_SHOP_ID, shop_name="Steam", price=last.price,
                            regular=last.regular, cut=last.cut, timestamp=last.timestamp,
                            store_low=Money(amount_minor=low, currency=last.price.currency),
                            url=f"https://example.invalid/fake/steam/app/{g.steam_app_id}/",
                        )
                    ],
                )
            )  # fmt: skip
        return results
