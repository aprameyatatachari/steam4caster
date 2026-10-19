"""User-requested price refreshes: the button and page loads."""

from __future__ import annotations

from datetime import timedelta

import httpx

from app.core.container import Container, RecordingDispatcher
from app.core.kv import MemoryKV
from app.core.timeutil import utcnow
from app.providers.pricing.caching import CacheTTLs, CachingPriceProvider
from tests.conftest import register
from tests.helpers import ScriptedProvider, history_point


def scripted(container: Container) -> ScriptedProvider:
    provider = ScriptedProvider()
    now = utcnow()
    provider.series["US"] = [history_point(now - timedelta(days=300), 0)]
    # Behind the real read-through cache, as in production.
    container.price_provider = CachingPriceProvider(provider, MemoryKV(), CacheTTLs())
    return provider


async def watch_scripted(client: httpx.AsyncClient, auth: dict[str, str]) -> str:
    game = (
        await client.get("/api/v1/games/search", params={"q": "scripted"}, headers=auth)
    ).json()[0]
    created = await client.post("/api/v1/watchlist", json={"game_id": game["id"]}, headers=auth)
    assert created.status_code == 201, created.text
    return str(game["id"])


async def test_refresh_reads_past_the_cache_and_queues_changed_games(
    client: httpx.AsyncClient, container: Container
) -> None:
    provider = scripted(container)
    auth = await register(client, "refresh@example.com")
    game_id = await watch_scripted(client, auth)
    assert (await client.get("/api/v1/watchlist", headers=auth)).json()[0]["current"][
        "discount_pct"
    ] == 0

    # A sale starts. The cached price would hide it for 30 minutes; a refresh must not.
    provider.series["US"].append(history_point(utcnow(), 50))
    container.settings.price_manual_min_age_seconds = 0
    assert isinstance(container.tasks, RecordingDispatcher)
    container.tasks.calls.clear()
    response = await client.post("/api/v1/watchlist/refresh-prices", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["entries"], body["checked"], body["changed"]) == (1, 1, 1)
    entry = (await client.get("/api/v1/watchlist", headers=auth)).json()[0]
    assert entry["current"]["discount_pct"] == 50
    # The change goes on to a new forecast and an alert check.
    assert container.tasks.calls == [("prices.process_series", (game_id, "US"))]


async def test_repeated_refreshes_do_not_requery_a_price_checked_moments_ago(
    client: httpx.AsyncClient, container: Container
) -> None:
    provider = scripted(container)
    auth = await register(client, "reload@example.com")
    await watch_scripted(client, auth)
    before = provider.calls.get("prices", 0)
    body = (await client.post("/api/v1/watchlist/refresh-prices", headers=auth)).json()
    assert (body["checked"], body["recently_checked"]) == (0, 1)
    assert provider.calls.get("prices", 0) == before


async def test_single_game_refresh_returns_the_new_price(
    client: httpx.AsyncClient, container: Container
) -> None:
    provider = scripted(container)
    auth = await register(client, "game@example.com")
    game_id = await watch_scripted(client, auth)
    provider.series["US"].append(history_point(utcnow(), 75))
    container.settings.price_manual_min_age_seconds = 0
    response = await client.post(f"/api/v1/games/{game_id}/prices/refresh", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["discount_pct"] == 75


async def test_refreshes_are_rate_limited_per_user(
    client: httpx.AsyncClient, container: Container
) -> None:
    scripted(container)
    container.settings.rate_limit_price_refresh_per_minute = 2
    auth = await register(client, "limit@example.com")
    codes = [
        (await client.post("/api/v1/watchlist/refresh-prices", headers=auth)).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]
