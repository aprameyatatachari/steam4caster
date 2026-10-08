from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from app.core.container import Container
from app.providers.fx import FrankfurterFxProvider, FxUnavailable, StaticFxProvider

BASE = "https://api.frankfurter.dev/v1"


@respx.mock
async def test_frankfurter_parses_rate_and_date() -> None:
    route = respx.get(f"{BASE}/latest").mock(
        return_value=httpx.Response(
            200, json={"amount": 1.0, "base": "USD", "date": "2026-10-08", "rates": {"INR": 96.78}}
        )
    )
    provider = FrankfurterFxProvider(BASE)
    rate = await provider.get_rate("USD", "INR")
    await provider.aclose()
    assert (rate.base, rate.quote, rate.rate, rate.as_of) == (
        "USD",
        "INR",
        96.78,
        date(2026, 10, 8),
    )
    assert dict(route.calls.last.request.url.params) == {"base": "USD", "symbols": "INR"}


@respx.mock
async def test_frankfurter_failures_are_unavailable() -> None:
    respx.get(f"{BASE}/latest").mock(
        side_effect=[
            httpx.Response(404),
            httpx.Response(200, json={"rates": {}}),
            httpx.ConnectError("down"),
        ]
    )
    provider = FrankfurterFxProvider(BASE)
    for _ in range(3):
        with pytest.raises(FxUnavailable):
            await provider.get_rate("USD", "ZZZ")
    await provider.aclose()


async def test_fx_endpoint_caches_and_requires_auth(
    client: httpx.AsyncClient, auth: dict[str, str], container: Container
) -> None:
    fx = StaticFxProvider({("USD", "INR"): 96.78}, date(2026, 10, 8))
    container.fx = fx
    assert (
        await client.get("/api/v1/fx", params={"base": "USD", "quote": "INR"})
    ).status_code == 401

    first = await client.get("/api/v1/fx", params={"base": "usd", "quote": "inr"}, headers=auth)
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["base"], body["quote"], body["rate"], body["as_of"]) == (
        "USD",
        "INR",
        96.78,
        "2026-10-08",
    )
    assert "Indicative" in body["note"] and body["source"]
    await client.get("/api/v1/fx", params={"base": "USD", "quote": "INR"}, headers=auth)
    assert fx.calls == 1  # second read came from the cache

    same = await client.get("/api/v1/fx", params={"base": "INR", "quote": "INR"}, headers=auth)
    assert same.json()["rate"] == 1.0 and fx.calls == 1

    missing = await client.get("/api/v1/fx", params={"base": "USD", "quote": "JPY"}, headers=auth)
    assert missing.status_code == 503
    bad = await client.get("/api/v1/fx", params={"base": "US", "quote": "INR"}, headers=auth)
    assert bad.status_code == 422
