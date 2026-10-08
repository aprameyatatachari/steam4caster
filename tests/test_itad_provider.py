"""Contract tests for the IsThereAnyDeal client against mocked HTTP responses whose
shapes follow the published OpenAPI document."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.core.kv import MemoryKV
from app.providers.pricing import itad
from app.providers.pricing.base import (
    ProviderAuthError,
    ProviderBadResponse,
    ProviderRateLimited,
    ProviderUnavailable,
)
from app.providers.pricing.caching import CacheTTLs, CachingPriceProvider
from app.providers.pricing.itad import IsThereAnyDealProvider, parse_retry_after

BASE = "https://api.isthereanydeal.com"
API_KEY = "super-secret-itad-key"
GID = "018d937f-590c-728b-ac35-38bcff85f086"

GAME = {
    "id": GID, "slug": "baldurs-gate-3", "title": "Baldur's Gate 3", "type": "game",
    "mature": False,
    "assets": {"boxart": "https://assets.example/boxart.jpg", "banner600": "https://assets.example/b.jpg"},
}  # fmt: skip
PRICE = {"amount": 1499.5, "amountInt": 149950, "currency": "INR"}
REGULAR = {"amount": 2999.0, "amountInt": 299900, "currency": "INR"}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(itad.asyncio, "sleep", fake_sleep)
    return delays


@pytest.fixture
async def provider():  # type: ignore[no-untyped-def]
    client = IsThereAnyDealProvider(
        API_KEY, base_url=BASE, user_agent="Steam4Caster/test", max_retries=2
    )
    yield client
    await client.aclose()


@respx.mock
async def test_search_sends_key_in_header_and_normalizes(provider: IsThereAnyDealProvider) -> None:
    route = respx.get(f"{BASE}/games/search/v1").mock(return_value=httpx.Response(200, json=[GAME]))
    (game,) = await provider.search_games("baldur", limit=5)
    request = route.calls.last.request
    assert request.headers["ITAD-API-Key"] == API_KEY
    assert request.headers["User-Agent"] == "Steam4Caster/test"
    assert API_KEY not in str(request.url)  # never in the query string
    assert dict(request.url.params) == {"title": "baldur", "results": "5"}
    assert game.provider_id == GID and game.title == "Baldur's Gate 3"
    assert game.assets["boxart"].endswith("boxart.jpg")


@respx.mock
async def test_lookup_by_steam_app_id(provider: IsThereAnyDealProvider) -> None:
    route = respx.get(f"{BASE}/games/lookup/v1").mock(
        side_effect=[
            httpx.Response(200, json={"found": True, "game": GAME}),
            httpx.Response(200, json={"found": False}),
        ]
    )
    found = await provider.lookup_game(steam_app_id=1086940)
    assert found is not None and found.provider_id == GID
    assert route.calls[0].request.url.params["appid"] == "1086940"
    assert await provider.lookup_game(title="Nope") is None
    with pytest.raises(ValueError):
        await provider.lookup_game()


@respx.mock
async def test_game_info_maps_steam_app_id_separately(provider: IsThereAnyDealProvider) -> None:
    respx.get(f"{BASE}/games/info/v2").mock(
        return_value=httpx.Response(
            200,
            json={
                **GAME,
                "earlyAccess": False,
                "achievements": True,
                "tradingCards": True,
                "appid": 1086940,
                "tags": ["RPG", "Story Rich"],
                "releaseDate": "2023-08-03",
                "developers": [{"id": 1, "name": "Larian Studios"}],
                "publishers": [{"id": 1, "name": "Larian Studios"}],
                "reviews": [],
                "stats": {},
                "players": None,
                "urls": {"game": "https://isthereanydeal.com/game/baldurs-gate-3/"},
            },
        )
    )
    info = await provider.get_game_info(GID)
    assert info is not None
    assert (info.provider_id, info.steam_app_id) == (GID, 1086940)
    assert info.publishers == ["Larian Studios"] and info.tags[0] == "RPG"
    assert info.release_date is not None and info.release_date.year == 2023


@respx.mock
async def test_game_info_404_is_none(provider: IsThereAnyDealProvider) -> None:
    respx.get(f"{BASE}/games/info/v2").mock(
        return_value=httpx.Response(404, json={"status_code": 404, "reason_phrase": "Not Found"})
    )
    assert await provider.get_game_info(GID) is None


@respx.mock
async def test_prices_are_batched_and_passed_through_unaltered(
    provider: IsThereAnyDealProvider,
) -> None:
    affiliate = "https://itad.link/01234567/?utm=affiliate-tag"

    def handler(request: httpx.Request) -> httpx.Response:
        ids = json.loads(request.content)
        return httpx.Response(
            200,
            json=[
                {
                    "id": gid,
                    "historyLow": {"all": PRICE, "y1": PRICE, "m3": None},
                    "deals": [
                        {
                            "shop": {"id": 61, "name": "Steam"}, "price": PRICE,
                            "regular": REGULAR, "cut": 50, "voucher": None,
                            "storeLow": {"amount": 1199.6, "amountInt": 119960, "currency": "INR"},
                            "flag": None, "drm": [], "platforms": [],
                            "timestamp": "2026-09-30T17:00:00+00:00", "expiry": None,
                            "url": affiliate,
                        }
                    ],
                }
                for gid in ids
            ],
        )  # fmt: skip

    route = respx.post(f"{BASE}/games/prices/v3").mock(side_effect=handler)
    ids = [f"id-{i}" for i in range(450)]
    results = await provider.get_current_prices(ids, "IN", [61])
    assert route.call_count == 3  # 200 + 200 + 50
    assert [len(json.loads(c.request.content)) for c in route.calls] == [200, 200, 50]
    assert route.calls[0].request.url.params["country"] == "IN"
    assert route.calls[0].request.url.params["shops"] == "61"
    deal = results[0].deals[0]
    assert deal.price.amount_minor == 149950 and deal.price.currency == "INR"  # integer minor units
    assert deal.regular.amount_minor == 299900 and deal.cut == 50
    assert deal.store_low is not None and deal.store_low.amount_minor == 119960
    assert deal.url == affiliate  # affiliate URL untouched


@respx.mock
async def test_history_always_sends_since_and_skips_null_deals(
    provider: IsThereAnyDealProvider,
) -> None:
    route = respx.get(f"{BASE}/games/history/v2").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "timestamp": "2026-07-10T17:00:00+00:00",
                    "shop": {"id": 61, "name": "Steam"},
                    "deal": {"price": REGULAR, "regular": REGULAR, "cut": 0},
                },
                {
                    "timestamp": "2026-06-26T17:00:00+00:00",
                    "shop": {"id": 61, "name": "Steam"},
                    "deal": {"price": PRICE, "regular": REGULAR, "cut": 50},
                },
                {
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "shop": {"id": 61, "name": "Steam"},
                    "deal": None,
                },
            ],
        )
    )
    since = datetime(2015, 1, 1, tzinfo=UTC)
    points = await provider.get_price_history(GID, "IN", [61], since)
    assert route.calls.last.request.url.params["since"] == "2015-01-01T00:00:00Z"
    assert [p.cut for p in points] == [50, 0]  # chronological, null deal dropped
    assert points[0].timestamp < points[1].timestamp


@respx.mock
async def test_shops_listing(provider: IsThereAnyDealProvider) -> None:
    respx.get(f"{BASE}/service/shops/v1").mock(
        return_value=httpx.Response(
            200, json=[{"id": 61, "title": "Steam", "deals": 1, "games": 2, "update": None}]
        )
    )
    (shop,) = await provider.list_shops("IN")
    assert (shop.provider_shop_id, shop.name) == (61, "Steam")


@respx.mock
async def test_429_respects_retry_after_then_succeeds(
    provider: IsThereAnyDealProvider, no_sleep: list[float]
) -> None:
    route = respx.get(f"{BASE}/games/search/v1").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=[GAME]),
        ]
    )
    assert len(await provider.search_games("x")) == 1
    assert route.call_count == 2
    assert 7 <= no_sleep[0] <= 7 * 1.25  # waited at least Retry-After, plus bounded jitter


@respx.mock
async def test_long_retry_after_is_surfaced_instead_of_blocking(
    provider: IsThereAnyDealProvider, no_sleep: list[float]
) -> None:
    route = respx.get(f"{BASE}/games/search/v1").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "600"})
    )
    with pytest.raises(ProviderRateLimited) as excinfo:
        await provider.search_games("x")
    assert excinfo.value.retry_after == 600
    assert route.call_count == 1 and no_sleep == []


@respx.mock
async def test_retries_are_bounded_on_server_errors(
    provider: IsThereAnyDealProvider, no_sleep: list[float]
) -> None:
    route = respx.get(f"{BASE}/games/search/v1").mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderUnavailable):
        await provider.search_games("x")
    assert route.call_count == 3  # 1 attempt + max_retries(2)
    assert len(no_sleep) == 2 and no_sleep[1] > no_sleep[0] * 0.9


@respx.mock
async def test_timeouts_retry_then_raise_unavailable(provider: IsThereAnyDealProvider) -> None:
    route = respx.get(f"{BASE}/games/search/v1").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderUnavailable):
        await provider.search_games("x")
    assert route.call_count == 3


@respx.mock
async def test_auth_and_client_errors_are_not_retried_and_never_leak_the_key(
    provider: IsThereAnyDealProvider,
) -> None:
    route = respx.get(f"{BASE}/games/search/v1").mock(return_value=httpx.Response(403))
    with pytest.raises(ProviderAuthError) as excinfo:
        await provider.search_games("x")
    assert route.call_count == 1
    assert API_KEY not in str(excinfo.value)
    respx.get(f"{BASE}/games/lookup/v1").mock(return_value=httpx.Response(400))
    with pytest.raises(ProviderBadResponse):
        await provider.lookup_game(title="x")


@respx.mock
async def test_malformed_payload_is_a_bad_response(provider: IsThereAnyDealProvider) -> None:
    respx.get(f"{BASE}/games/search/v1").mock(
        return_value=httpx.Response(200, json=[{"unexpected": True}])
    )
    with pytest.raises(ProviderBadResponse):
        await provider.search_games("x")


def test_retry_after_parsing() -> None:
    assert parse_retry_after("12") == 12
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0  # in the past


@respx.mock
async def test_cache_serves_repeat_reads_and_only_fetches_missing_prices(
    provider: IsThereAnyDealProvider,
) -> None:
    cached = CachingPriceProvider(provider, MemoryKV(), CacheTTLs())
    search = respx.get(f"{BASE}/games/search/v1").mock(
        return_value=httpx.Response(200, json=[GAME])
    )
    await cached.search_games("Baldur")
    await cached.search_games("  baldur ")  # normalised to the same cache entry
    assert search.call_count == 1

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"id": g, "historyLow": {"all": None, "y1": None, "m3": None}, "deals": []}
                       for g in json.loads(request.content)],
        )  # fmt: skip

    prices = respx.post(f"{BASE}/games/prices/v3").mock(side_effect=handler)
    await cached.get_current_prices(["a", "b"], "IN", [61])
    await cached.get_current_prices(["a", "b", "c"], "IN", [61])
    assert [json.loads(c.request.content) for c in prices.calls] == [["a", "b"], ["c"]]
    await cached.get_current_prices(["a"], "US", [61])  # a different region is a different entry
    assert prices.call_count == 3
