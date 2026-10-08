"""Reference exchange rates.

Used only to show an indicative conversion next to prices from another region. Rates
never feed stored prices, forecasts or recommendations: a regional price is always the
provider's own observation for that country.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import httpx

SOURCE_NAME = "European Central Bank reference rates via Frankfurter"
SOURCE_URL = "https://frankfurter.dev/"


class FxUnavailable(Exception):
    """The rate source is unreachable or does not quote this currency pair."""


@dataclass(frozen=True)
class FxRate:
    base: str
    quote: str
    rate: float
    as_of: date


class FxRateProvider(Protocol):
    async def get_rate(self, base: str, quote: str) -> FxRate: ...

    async def aclose(self) -> None: ...


class FrankfurterFxProvider:
    """Daily reference rates from the keyless Frankfurter API."""

    def __init__(
        self,
        base_url: str = "https://api.frankfurter.dev/v1",
        *,
        user_agent: str = "Steam4Caster/0.1",
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_rate(self, base: str, quote: str) -> FxRate:
        try:
            response = await self._client.get("/latest", params={"base": base, "symbols": quote})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise FxUnavailable("rate source unreachable") from exc
        if response.status_code != 200:
            raise FxUnavailable(f"rate source returned HTTP {response.status_code}")
        try:
            payload = response.json()
            rate = float(payload["rates"][quote])
            as_of = date.fromisoformat(payload["date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FxUnavailable("unexpected rate payload") from exc
        if rate <= 0:
            raise FxUnavailable("non-positive rate")
        return FxRate(base=base, quote=quote, rate=rate, as_of=as_of)


class StaticFxProvider:
    """Fixed rates for tests."""

    def __init__(self, rates: dict[tuple[str, str], float], as_of: date) -> None:
        self._rates = rates
        self._as_of = as_of
        self.calls = 0

    async def aclose(self) -> None:
        return None

    async def get_rate(self, base: str, quote: str) -> FxRate:
        self.calls += 1
        if (base, quote) not in self._rates:
            raise FxUnavailable("pair not quoted")
        return FxRate(base=base, quote=quote, rate=self._rates[(base, quote)], as_of=self._as_of)
