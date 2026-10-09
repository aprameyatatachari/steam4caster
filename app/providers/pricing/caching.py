"""Read-through cache decorator for any ``PriceDataProvider``.

Stable metadata is cached far longer than current prices. Only normalized internal
schemas are cached, never credentials or raw provider payloads.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel, TypeAdapter

from app.core.kv import KeyValueStore
from app.core.metrics import PROVIDER_CACHE
from app.providers.pricing.base import (
    PriceDataProvider,
    ProviderGame,
    ProviderGameInfo,
    ProviderGamePrices,
    ProviderHistoryPoint,
    ProviderShop,
)

T = TypeVar("T")

_NULL = "\x00null"


class CacheTTLs(BaseModel):
    search: int = 6 * 3600
    metadata: int = 7 * 24 * 3600
    shops: int = 24 * 3600
    prices: int = 30 * 60
    history: int = 3 * 3600


def _key(operation: str, *parts: Any) -> str:
    digest = hashlib.sha256(json.dumps(parts, default=str, sort_keys=True).encode()).hexdigest()
    return f"provider:{operation}:{digest[:32]}"


class CachingPriceProvider:
    def __init__(self, inner: PriceDataProvider, kv: KeyValueStore, ttls: CacheTTLs) -> None:
        self._inner = inner
        self._kv = kv
        self._ttls = ttls
        self.name = inner.name

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def _cached(
        self,
        operation: str,
        key: str,
        ttl: int,
        adapter: TypeAdapter[T],
        fetch: Callable[[], Awaitable[T]],
    ) -> T:
        raw = await self._kv.get(key)
        if raw is not None:
            PROVIDER_CACHE.labels(operation, "hit").inc()
            return adapter.validate_json("null" if raw == _NULL else raw)
        PROVIDER_CACHE.labels(operation, "miss").inc()
        value = await fetch()
        encoded = adapter.dump_json(value).decode()
        await self._kv.set(key, _NULL if encoded == "null" else encoded, ttl)
        return value

    async def search_games(self, title: str, limit: int = 20) -> list[ProviderGame]:
        normalized = " ".join(title.lower().split())
        return await self._cached(
            "search",
            _key("search", normalized, limit),
            self._ttls.search,
            TypeAdapter(list[ProviderGame]),
            lambda: self._inner.search_games(title, limit),
        )

    async def lookup_game(
        self, *, title: str | None = None, steam_app_id: int | None = None
    ) -> ProviderGame | None:
        return await self._cached(
            "lookup",
            _key("lookup", title.lower().strip() if title else None, steam_app_id),
            self._ttls.metadata,
            TypeAdapter(ProviderGame | None),
            lambda: self._inner.lookup_game(title=title, steam_app_id=steam_app_id),
        )

    async def get_game_info(self, provider_id: str) -> ProviderGameInfo | None:
        return await self._cached(
            "info",
            _key("info", provider_id),
            self._ttls.metadata,
            TypeAdapter(ProviderGameInfo | None),
            lambda: self._inner.get_game_info(provider_id),
        )

    async def get_current_prices(
        self, provider_ids: Sequence[str], country: str, shop_ids: Sequence[int]
    ) -> list[ProviderGamePrices]:
        """Per-game cache entries so a batch only fetches the games that missed."""
        adapter = TypeAdapter(ProviderGamePrices)
        shops = sorted(shop_ids)
        found: dict[str, ProviderGamePrices] = {}
        missing: list[str] = []
        for provider_id in dict.fromkeys(provider_ids):
            raw = await self._kv.get(_key("prices", provider_id, country, shops))
            if raw is None:
                PROVIDER_CACHE.labels("prices", "miss").inc()
                missing.append(provider_id)
            else:
                PROVIDER_CACHE.labels("prices", "hit").inc()
                found[provider_id] = adapter.validate_json(raw)
        if missing:
            fetched = {
                p.provider_id: p
                for p in await self._inner.get_current_prices(missing, country, shop_ids)
            }
            for provider_id in missing:
                # A game absent from the response has no deals in this region.
                value = fetched.get(provider_id) or ProviderGamePrices(provider_id=provider_id)
                found[provider_id] = value
                await self._kv.set(
                    _key("prices", provider_id, country, shops),
                    adapter.dump_json(value).decode(),
                    self._ttls.prices,
                )
        return [found[p] for p in dict.fromkeys(provider_ids)]

    async def get_price_history(
        self, provider_id: str, country: str, shop_ids: Sequence[int], since: datetime
    ) -> list[ProviderHistoryPoint]:
        # Bucket ``since`` to the hour so near-identical incremental reads share an entry.
        bucket = since.replace(minute=0, second=0, microsecond=0).isoformat()
        return await self._cached(
            "history",
            _key("history", provider_id, country, sorted(shop_ids), bucket),
            self._ttls.history,
            TypeAdapter(list[ProviderHistoryPoint]),
            lambda: self._inner.get_price_history(provider_id, country, shop_ids, since),
        )

    async def list_popular_games(self, limit: int = 100, offset: int = 0) -> list[ProviderGame]:
        return await self._cached(
            "popular",
            _key("popular", limit, offset),
            self._ttls.shops,
            TypeAdapter(list[ProviderGame]),
            lambda: self._inner.list_popular_games(limit, offset),
        )

    async def list_shops(self, country: str) -> list[ProviderShop]:
        return await self._cached(
            "shops",
            _key("shops", country),
            self._ttls.shops,
            TypeAdapter(list[ProviderShop]),
            lambda: self._inner.list_shops(country),
        )
