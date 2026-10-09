"""IsThereAnyDeal API client.

Shapes follow the live OpenAPI document (https://docs.isthereanydeal.com/openapi.json):
``GET /games/search/v1``, ``GET /games/lookup/v1``, ``GET /games/info/v2``,
``POST /games/prices/v3``, ``GET /games/history/v2`` and ``GET /service/shops/v1``.
Provider prices and URLs are passed through unaltered.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.logging import get_logger
from app.core.metrics import PROVIDER_LATENCY, PROVIDER_RATE_LIMITED, PROVIDER_REQUESTS
from app.providers.pricing.base import (
    Money,
    ProviderAuthError,
    ProviderBadResponse,
    ProviderDeal,
    ProviderError,
    ProviderGame,
    ProviderGameInfo,
    ProviderGamePrices,
    ProviderHistoryPoint,
    ProviderRateLimited,
    ProviderShop,
    ProviderUnavailable,
)

logger = get_logger(__name__)

PRICES_BATCH_SIZE = 200  # documented maximum for POST /games/prices/v3


def _money(raw: dict[str, Any]) -> Money:
    return Money(amount_minor=int(raw["amountInt"]), currency=str(raw["currency"]).upper())


def _game(raw: dict[str, Any]) -> ProviderGame:
    return ProviderGame(
        provider_id=str(raw["id"]),
        slug=str(raw["slug"]),
        title=str(raw["title"]),
        type=raw.get("type"),
        mature=bool(raw.get("mature", False)),
        assets={k: str(v) for k, v in (raw.get("assets") or {}).items() if v},
    )


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        return max(0.0, (when - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


class IsThereAnyDealProvider:
    name = "itad"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.isthereanydeal.com",
        user_agent: str = "Steam4Caster/0.1",
        timeout: float = 15.0,
        max_retries: int = 3,
        max_retry_after: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._max_retries = max_retries
        self._max_retry_after = max_retry_after
        # The key travels in a header (never the query string) so it cannot leak via URLs.
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
            headers={
                "ITAD-API-Key": api_key,
                "User-Agent": user_agent,
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        allow_404: bool = False,
    ) -> Any:
        last_error: ProviderError = ProviderUnavailable("provider request failed")
        for attempt in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                response = await self._client.request(method, path, params=params, json=json)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                PROVIDER_REQUESTS.labels(self.name, operation, "transport_error").inc()
                last_error = ProviderUnavailable(type(exc).__name__)
                delay: float | None = None
            else:
                PROVIDER_LATENCY.labels(self.name, operation).observe(time.perf_counter() - started)
                status = response.status_code
                if status == 429:
                    PROVIDER_RATE_LIMITED.labels(self.name, operation).inc()
                    PROVIDER_REQUESTS.labels(self.name, operation, "rate_limited").inc()
                    retry_after = parse_retry_after(response.headers.get("Retry-After"))
                    last_error = ProviderRateLimited(retry_after)
                    if retry_after is not None and retry_after > self._max_retry_after:
                        # Waiting inline would hold a worker; let the caller reschedule.
                        raise last_error
                    delay = retry_after
                elif status >= 500:
                    PROVIDER_REQUESTS.labels(self.name, operation, "server_error").inc()
                    last_error = ProviderUnavailable(f"HTTP {status}")
                    delay = None
                elif status in (401, 403):
                    PROVIDER_REQUESTS.labels(self.name, operation, "auth_error").inc()
                    raise ProviderAuthError("provider rejected the configured credentials")
                elif status == 404 and allow_404:
                    PROVIDER_REQUESTS.labels(self.name, operation, "not_found").inc()
                    return None
                elif status >= 400:
                    PROVIDER_REQUESTS.labels(self.name, operation, "client_error").inc()
                    raise ProviderBadResponse(f"HTTP {status}")
                else:
                    PROVIDER_REQUESTS.labels(self.name, operation, "ok").inc()
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise ProviderBadResponse("response was not valid JSON") from exc
            if attempt >= self._max_retries:
                break
            if delay is None:
                delay = min(8.0, 0.5 * 2**attempt)
            await asyncio.sleep(delay + random.uniform(0, 0.25 * max(delay, 0.1)))
            logger.warning(
                "retrying provider request",
                extra={"operation": operation, "attempt": attempt + 1, "error": last_error.code},
            )
        raise last_error

    @staticmethod
    def _parse(operation: str, parser: Any, payload: Any) -> Any:
        try:
            return parser(payload)
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            logger.error("unexpected provider payload", extra={"operation": operation})
            raise ProviderBadResponse(f"unexpected {operation} payload") from exc

    async def search_games(self, title: str, limit: int = 20) -> list[ProviderGame]:
        payload = await self._request(
            "search", "GET", "/games/search/v1", params={"title": title, "results": limit}
        )
        return self._parse("search", lambda p: [_game(g) for g in p], payload)

    async def lookup_game(
        self, *, title: str | None = None, steam_app_id: int | None = None
    ) -> ProviderGame | None:
        if (title is None) == (steam_app_id is None):
            raise ValueError("provide exactly one of title or steam_app_id")
        params: dict[str, Any] = {"title": title} if title else {"appid": steam_app_id}
        payload = await self._request("lookup", "GET", "/games/lookup/v1", params=params)

        def parse(p: dict[str, Any]) -> ProviderGame | None:
            return _game(p["game"]) if p.get("found") and p.get("game") else None

        return self._parse("lookup", parse, payload)

    async def get_game_info(self, provider_id: str) -> ProviderGameInfo | None:
        payload = await self._request(
            "info", "GET", "/games/info/v2", params={"id": provider_id}, allow_404=True
        )
        if payload is None:
            return None

        def parse(p: dict[str, Any]) -> ProviderGameInfo:
            base = _game(p)
            return ProviderGameInfo(
                **base.model_dump(),
                steam_app_id=p.get("appid"),
                early_access=p.get("earlyAccess"),
                release_date=p.get("releaseDate") or None,
                developers=[str(c["name"]) for c in p.get("developers") or []],
                publishers=[str(c["name"]) for c in p.get("publishers") or []],
                tags=[str(t) for t in p.get("tags") or []],
                url=(p.get("urls") or {}).get("game"),
            )

        return self._parse("info", parse, payload)

    async def get_current_prices(
        self, provider_ids: Sequence[str], country: str, shop_ids: Sequence[int]
    ) -> list[ProviderGamePrices]:
        results: list[ProviderGamePrices] = []
        ids = list(dict.fromkeys(provider_ids))
        for start in range(0, len(ids), PRICES_BATCH_SIZE):
            batch = ids[start : start + PRICES_BATCH_SIZE]
            params: dict[str, Any] = {"country": country}
            if shop_ids:
                params["shops"] = ",".join(str(s) for s in shop_ids)
            payload = await self._request(
                "prices", "POST", "/games/prices/v3", params=params, json=batch
            )

            def parse(p: list[dict[str, Any]]) -> list[ProviderGamePrices]:
                return [
                    ProviderGamePrices(
                        provider_id=str(item["id"]),
                        deals=[
                            ProviderDeal(
                                shop_id=int(d["shop"]["id"]),
                                shop_name=str(d["shop"]["name"]),
                                price=_money(d["price"]),
                                regular=_money(d["regular"]),
                                cut=int(d["cut"]),
                                store_low=_money(d["storeLow"]) if d.get("storeLow") else None,
                                timestamp=d["timestamp"],
                                expiry=d.get("expiry"),
                                url=d.get("url"),
                            )
                            for d in item.get("deals") or []
                        ],
                    )
                    for item in p
                ]

            results.extend(self._parse("prices", parse, payload))
        return results

    async def get_price_history(
        self, provider_id: str, country: str, shop_ids: Sequence[int], since: datetime
    ) -> list[ProviderHistoryPoint]:
        # Without ``since`` the endpoint only returns roughly the last three months.
        params: dict[str, Any] = {
            "id": provider_id,
            "country": country,
            "since": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if shop_ids:
            params["shops"] = ",".join(str(s) for s in shop_ids)
        payload = await self._request("history", "GET", "/games/history/v2", params=params)

        def parse(p: list[dict[str, Any]]) -> list[ProviderHistoryPoint]:
            points = [
                ProviderHistoryPoint(
                    timestamp=item["timestamp"],
                    shop_id=int(item["shop"]["id"]),
                    price=_money(item["deal"]["price"]),
                    regular=_money(item["deal"]["regular"]),
                    cut=round(float(item["deal"]["cut"])),
                )
                for item in p
                # ``deal`` is null when the game was unavailable in the shop at that time.
                if item.get("deal")
            ]
            return sorted(points, key=lambda pt: pt.timestamp)

        return self._parse("history", parse, payload)

    async def list_popular_games(self, limit: int = 100, offset: int = 0) -> list[ProviderGame]:
        """The provider's most popular games, used to pick a training catalogue."""
        payload = await self._request(
            "popular",
            "GET",
            "/stats/most-popular/v1",
            params={"limit": min(limit, 500), "offset": offset},
        )
        return self._parse("popular", lambda p: [_game({**g, "assets": {}}) for g in p], payload)

    async def list_shops(self, country: str) -> list[ProviderShop]:
        payload = await self._request(
            "shops", "GET", "/service/shops/v1", params={"country": country}
        )
        return self._parse(
            "shops",
            lambda p: [
                ProviderShop(provider_shop_id=int(s["id"]), name=str(s["title"])) for s in p
            ],
            payload,
        )
