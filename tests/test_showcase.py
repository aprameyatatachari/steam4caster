from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.core.container import Container
from app.core.timeutil import utcnow
from app.providers.pricing.base import (
    Money,
    ProviderDeal,
    ProviderGame,
    ProviderListedDeal,
    ProviderRateLimited,
)
from tests.helpers import GAME_ID, STEAM, ScriptedProvider, price_points, regular_sales, to_history


def listed(
    provider_id: str, title: str, cut: int, *, kind: str = "game", expiry: datetime | None = None
) -> ProviderListedDeal:
    return ProviderListedDeal(
        game=ProviderGame(provider_id=provider_id, slug=title.lower(), title=title, type=kind),
        deal=ProviderDeal(
            shop_id=STEAM,
            shop_name="Steam",
            price=Money(amount_minor=1000 * (100 - cut) // 100, currency="INR"),
            regular=Money(amount_minor=1000, currency="INR"),
            cut=cut,
            timestamp=datetime(2026, 10, 1, tzinfo=UTC),
            expiry=expiry,
            url=f"https://example.invalid/deal/{provider_id}?ref=keep",
        ),
    )


async def test_best_deals_need_no_account_and_keep_deep_full_game_sales(
    client: httpx.AsyncClient, container: Container
) -> None:
    provider = ScriptedProvider()
    ends = datetime(2026, 10, 20, 17, 0, tzinfo=UTC)
    provider.deals = [
        listed("a", "Alpha", 75, expiry=ends),
        listed("b", "Shallow", 20),
        listed("c", "Alpha Soundtrack", 90, kind="dlc"),
        listed("d", "Delta", 50),
    ]
    container.price_provider = provider

    response = await client.get("/api/v1/public/deals", params={"country": "IN"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [d["game"]["title"] for d in body["items"]] == ["Alpha", "Delta"]
    first, second = body["items"]
    assert first["regular"]["amount_minor"] == 1000 and first["price"]["amount_minor"] == 250
    assert first["discount_pct"] == 75 and first["price"]["currency"] == "INR"
    assert first["expires_at"].startswith("2026-10-20T17:00:00")
    assert second["expires_at"] is None  # an unknown end date stays unknown
    assert first["url"].endswith("?ref=keep")  # provider URL untouched
    assert body["attribution"]["provider"] == "IsThereAnyDeal"
    assert body["min_discount_pct"] == 50


async def test_public_routes_reject_regions_outside_the_short_list(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/public/deals", params={"country": "ZZ"})
    assert response.status_code == 422
    assert "IN" in response.json()["error"]["details"]["supported_countries"]


async def test_snapshot_returns_history_forecast_and_call_without_an_account(
    client: httpx.AsyncClient, container: Container
) -> None:
    provider = ScriptedProvider()
    now = utcnow()
    provider.series["IN"] = to_history(
        price_points(now, sales=regular_sales(10, 60, 30), regular=1000), "INR"
    )
    container.price_provider = provider

    # Only a game on this week's list can be opened without an account.
    provider.deals = [listed(GAME_ID, "Scripted Quest", 50)]
    deals = await client.get("/api/v1/public/deals", params={"country": "IN"})
    game = deals.json()["items"][0]["game"]
    assert game["itad_id"] == GAME_ID

    response = await client.get(
        f"/api/v1/public/games/{game['id']}/snapshot",
        params={"country": "IN", "max_wait_days": 60},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["price"]["currency"] == "INR"
    assert len(body["history"]) > 10 and len(body["sale_events"]) > 5
    assert 0 <= body["forecast"]["sale_probability"]["days_30"] <= 1
    assert body["recommendation"]["action"] in {"BUY", "WAIT", "NEUTRAL"}
    assert body["recommendation"]["max_wait_days"] == 60

    bad = await client.get(
        f"/api/v1/public/games/{game['id']}/snapshot", params={"max_wait_days": 45}
    )
    assert bad.status_code == 422


async def test_snapshot_refuses_games_that_are_not_listed_deals(
    client: httpx.AsyncClient, container: Container, auth: dict[str, str]
) -> None:
    # A real game in the catalogue, found through the signed-in search...
    found = await client.get("/api/v1/games/search", params={"q": "Hollow"}, headers=auth)
    game = found.json()[0]
    # ...is still closed to the public demo unless it is one of this week's deals.
    provider = ScriptedProvider()
    provider.deals = [listed("a", "Alpha", 75)]
    container.price_provider = provider
    response = await client.get(f"/api/v1/public/games/{game['id']}/snapshot")
    assert response.status_code == 404
    assert "account" in response.json()["error"]["message"]
    assert (await client.get("/api/v1/public/search", params={"q": "ho"})).status_code == 404


async def test_deals_list_is_capped_at_ten(client: httpx.AsyncClient, container: Container) -> None:
    provider = ScriptedProvider()
    provider.deals = [listed(f"g{i}", f"Game {i}", 60) for i in range(14)]
    container.price_provider = provider
    body = (await client.get("/api/v1/public/deals")).json()
    assert len(body["items"]) == 10


async def test_public_calibration_is_empty_before_any_forecast_is_scored(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/public/calibration")
    assert response.status_code == 200
    assert response.json() == {"model_version": None, "horizon_days": 30, "n": 0, "bins": []}


async def test_public_routes_are_rate_limited_per_ip(
    client: httpx.AsyncClient, container: Container
) -> None:
    container.settings.rate_limit_public_per_minute = 2
    codes = [(await client.get("/api/v1/public/calibration")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


async def test_deals_fall_back_to_the_last_good_list_when_the_provider_fails(
    client: httpx.AsyncClient, container: Container
) -> None:
    provider = ScriptedProvider()
    provider.deals = [
        listed("a", "Alpha", 75),
        listed("b", "Ended", 60, expiry=datetime(2020, 1, 1, tzinfo=UTC)),
    ]
    container.price_provider = provider
    first = await client.get("/api/v1/public/deals")
    assert [d["game"]["title"] for d in first.json()["items"]] == ["Alpha", "Ended"]

    provider.failures = [ProviderRateLimited(600)]
    again = await client.get("/api/v1/public/deals")
    assert again.status_code == 200
    # The stored list is served, without the sale that has already ended.
    assert [d["game"]["title"] for d in again.json()["items"]] == ["Alpha"]


async def test_deals_report_the_outage_when_there_is_no_earlier_list(
    client: httpx.AsyncClient, container: Container
) -> None:
    provider = ScriptedProvider()
    container.price_provider = provider
    await client.get("/api/v1/public/calibration")  # resolves nothing from the provider
    provider.failures = [ProviderRateLimited(600), ProviderRateLimited(600)]
    response = await client.get("/api/v1/public/deals", params={"country": "US"})
    assert response.status_code == 503
