from __future__ import annotations

import httpx
from sqlalchemy import select

from app.core.container import Container, RecordingDispatcher
from app.models import WatchlistEntry
from tests.conftest import register
from tests.test_api_catalog import find_game


async def add(
    client: httpx.AsyncClient, auth: dict[str, str], game_id: str, **body: object
) -> httpx.Response:
    return await client.post("/api/v1/watchlist", json={"game_id": game_id, **body}, headers=auth)


async def test_watchlist_crud_and_preferences(
    client: httpx.AsyncClient, auth: dict[str, str], container: Container
) -> None:
    game = await find_game(client, auth)
    created = await add(
        client, auth, game["id"], country="IN", target_price_minor=30000, min_discount_pct=50,
        max_wait_days=60, historical_low_only=False, notify_on_buy=True, channels=["EMAIL"],
    )  # fmt: skip
    assert created.status_code == 201, created.text
    entry = created.json()
    assert entry["country"] == "IN" and entry["currency"] == "INR"  # learned from the region
    assert entry["target_price"] == {"amount_minor": 30000, "currency": "INR", "amount": "300.00"}
    assert entry["channels"] == ["EMAIL"] and entry["current"]["price"]["currency"] == "INR"
    assert entry["game"]["title"] == "Hollow Lantern"
    # Adding a game schedules its background processing.
    assert isinstance(container.tasks, RecordingDispatcher)
    assert container.tasks.calls == [("prices.process_series", (game["id"], "IN"))]

    assert (await add(client, auth, game["id"])).status_code == 409  # one entry per user+game

    url = f"/api/v1/watchlist/{entry['id']}"
    patched = await client.patch(
        url, json={"max_wait_days": 14, "target_price_minor": None, "channels": ["WEB_PUSH", "EMAIL"]},
        headers=auth,
    )  # fmt: skip
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["max_wait_days"] == 14 and body["target_price"] is None
    assert body["min_discount_pct"] == 50  # untouched fields are preserved
    assert body["channels"] == ["EMAIL", "WEB_PUSH"]

    listing = (await client.get("/api/v1/watchlist", headers=auth)).json()
    assert [e["id"] for e in listing] == [entry["id"]]
    assert (await client.get(url, headers=auth)).status_code == 200
    assert (await client.delete(url, headers=auth)).status_code == 204
    assert (await client.get(url, headers=auth)).status_code == 404
    assert (await client.get("/api/v1/watchlist", headers=auth)).json() == []


async def test_watchlist_validation(client: httpx.AsyncClient, auth: dict[str, str]) -> None:
    game = await find_game(client, auth)
    for bad in (
        {"min_discount_pct": 0},
        {"min_discount_pct": 101},
        {"max_wait_days": 0},
        {"max_wait_days": 366},
        {"target_price_minor": -1},
        {"channels": ["CARRIER_PIGEON"]},
        {"country": "INDIA"},
    ):
        response = await add(client, auth, game["id"], **bad)
        assert response.status_code == 422, bad
    missing = await add(client, auth, "00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


async def test_target_price_currency_must_match_the_region(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    game = await find_game(client, auth)
    mismatch = await add(
        client, auth, game["id"], country="IN", currency="USD", target_price_minor=999
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["details"] == {"expected_currency": "INR"}
    ok = await add(client, auth, game["id"], country="IN", currency="INR", target_price_minor=30000)
    assert ok.status_code == 201
    changed = await client.patch(
        f"/api/v1/watchlist/{ok.json()['id']}", json={"currency": "USD"}, headers=auth
    )
    assert changed.status_code == 422


async def test_users_cannot_see_or_modify_each_others_entries(
    client: httpx.AsyncClient, container: Container
) -> None:
    alice = await register(client, "alice@example.com")
    mallory = await register(client, "mallory@example.com")
    game = await find_game(client, alice)
    entry = (await add(client, alice, game["id"], min_discount_pct=30)).json()
    url = f"/api/v1/watchlist/{entry['id']}"

    assert (await client.get("/api/v1/watchlist", headers=mallory)).json() == []
    assert (await client.get(url, headers=mallory)).status_code == 404
    assert (
        await client.patch(url, json={"min_discount_pct": 1}, headers=mallory)
    ).status_code == 404
    assert (await client.delete(url, headers=mallory)).status_code == 404
    summary = (await client.get("/api/v1/watchlist/summary", headers=mallory)).json()
    assert summary["entries"] == 0 and summary["totals"] == []

    async with container.db.session() as session:
        row = (await session.scalars(select(WatchlistEntry))).one()
        assert row.min_discount_pct == 30  # untouched
    # Mallory can still watch the same game independently.
    assert (await add(client, mallory, game["id"])).status_code == 201


async def test_summary_groups_by_currency_and_labels_estimates(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    hollow = await find_game(client, auth, "Hollow")
    velocity = await find_game(client, auth, "Velocity")
    iron = await find_game(client, auth, "Iron")
    assert (await add(client, auth, hollow["id"], country="US")).status_code == 201
    assert (await add(client, auth, velocity["id"], country="US")).status_code == 201
    assert (await add(client, auth, iron["id"], country="IN")).status_code == 201
    for game, country in ((hollow, "US"), (velocity, "US"), (iron, "IN")):
        forecast = await client.get(
            f"/api/v1/games/{game['id']}/forecast", params={"country": country}, headers=auth
        )
        assert forecast.status_code == 200

    summary = (await client.get("/api/v1/watchlist/summary", headers=auth)).json()
    assert summary["entries"] == 3
    totals = {t["currency"]: t for t in summary["totals"]}
    assert set(totals) == {"USD", "INR"}  # never summed across currencies
    usd = totals["USD"]
    assert usd["games_priced"] == 2 and usd["games_with_forecast"] == 2
    assert usd["current_total"]["currency"] == "USD"
    assert usd["historical_low_total"]["amount_minor"] <= usd["current_total"]["amount_minor"]
    horizons = [e["horizon_days"] for e in usd["expected"]]
    assert horizons == [7, 30, 90]
    expected = [e["expected_total"]["amount_minor"] for e in usd["expected"]]
    assert expected == sorted(expected, reverse=True)  # longer horizon, lower expected cost
    assert all(e["expected_total"]["amount_minor"] <= usd["current_total"]["amount_minor"]
               for e in usd["expected"])  # fmt: skip
    assert all(e["is_estimate"] for e in usd["expected"])
    assert sum(summary["recommendation_counts"].values()) == 3
    assert "estimates" in summary["disclaimer"]
    for item in summary["likely_on_sale_within_30d"]:
        assert item["sale_probability_30d"] >= 0.6
