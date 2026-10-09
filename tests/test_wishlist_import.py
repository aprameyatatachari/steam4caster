from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from app.core.container import Container, RecordingDispatcher
from app.forecasting.calendar import next_fests, seasonal_windows
from app.providers.steam import (
    StaticSteamProvider,
    SteamCommunityProvider,
    SteamProfileNotFound,
    SteamUnavailable,
    parse_profile_reference,
)

STEAM_ID = "76561198000000001"
WISHLIST_URL = "https://api.steampowered.com/IWishlistService/GetWishlist/v1/"


# --- profile references -----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (STEAM_ID, ("id", STEAM_ID)),
        (f" https://steamcommunity.com/profiles/{STEAM_ID}/ ", ("id", STEAM_ID)),
        (f"steamcommunity.com/profiles/{STEAM_ID}/wishlist", ("id", STEAM_ID)),
        ("https://steamcommunity.com/id/some_name-1/", ("vanity", "some_name-1")),
        ("some_name", ("vanity", "some_name")),
    ],
)
def test_profile_reference_forms(value: str, expected: tuple[str, str]) -> None:
    assert parse_profile_reference(value) == expected


@pytest.mark.parametrize("value", ["", "x", "not a profile!", "https://example.com/id/x y"])
def test_profile_reference_rejects_garbage(value: str) -> None:
    with pytest.raises(SteamProfileNotFound):
        parse_profile_reference(value)


@respx.mock
async def test_steam_provider_resolves_vanity_and_orders_the_wishlist() -> None:
    respx.get("https://steamcommunity.com/id/some_name/").mock(
        side_effect=[
            httpx.Response(200, text=f"<profile><steamID64>{STEAM_ID}</steamID64></profile>"),
            httpx.Response(200, text="<response><error>not found</error></response>"),
        ]
    )
    wishlist = respx.get(WISHLIST_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "response": {
                        "items": [
                            {"appid": 30, "priority": 0, "date_added": 5},  # unranked: last
                            {"appid": 20, "priority": 2, "date_added": 9},
                            {"appid": 10, "priority": 1, "date_added": 7},
                        ]
                    }
                },
            ),
            httpx.Response(200, json={"response": {}}),  # private or empty
            httpx.Response(503),
        ]
    )
    provider = SteamCommunityProvider()
    assert await provider.resolve_steam_id("https://steamcommunity.com/id/some_name") == STEAM_ID
    with pytest.raises(SteamProfileNotFound):
        await provider.resolve_steam_id("some_name")
    assert await provider.get_wishlist_app_ids(STEAM_ID) == [10, 20, 30]
    assert wishlist.calls.last.request.url.params["steamid"] == STEAM_ID
    assert await provider.get_wishlist_app_ids(STEAM_ID) == []
    with pytest.raises(SteamUnavailable):
        await provider.get_wishlist_app_ids(STEAM_ID)
    await provider.aclose()


# --- import endpoint --------------------------------------------------------------


async def test_import_adds_new_games_and_is_safe_to_repeat(
    client: httpx.AsyncClient, auth: dict[str, str], container: Container
) -> None:
    # 900003 and 900006 exist in the demo catalogue; 111 is unknown to the provider.
    container.steam = StaticSteamProvider(
        {STEAM_ID: [900003, 111, 900006, 900003]}, vanities={"some_name": STEAM_ID}
    )
    assert isinstance(container.tasks, RecordingDispatcher)

    first = await client.post(
        "/api/v1/watchlist/import/steam", json={"profile": "some_name"}, headers=auth
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["steam_id"] == STEAM_ID and body["on_wishlist"] == 4
    assert (body["added"], body["already_watching"], body["not_found"], body["failed"]) == (
        2,
        0,
        1,
        0,
    )
    assert body["added_titles"] == ["Hollow Lantern", "Velocity Drift"]  # wishlist order
    # Each new entry is handed to the background job for prices, forecast and alerts.
    assert [name for name, _ in container.tasks.calls] == ["prices.process_series"] * 2

    watchlist = (await client.get("/api/v1/watchlist", headers=auth)).json()
    assert {e["game"]["steam_app_id"] for e in watchlist} == {900003, 900006}
    assert all(
        e["country"] == "US" and set(e["channels"]) == {"EMAIL", "WEB_PUSH"} for e in watchlist
    )

    again = await client.post(
        "/api/v1/watchlist/import/steam",
        json={"profile": f"https://steamcommunity.com/profiles/{STEAM_ID}"},
        headers=auth,
    )
    assert (again.json()["added"], again.json()["already_watching"]) == (0, 2)
    assert len((await client.get("/api/v1/watchlist", headers=auth)).json()) == 2


async def test_import_reports_private_or_missing_profiles_clearly(
    client: httpx.AsyncClient, auth: dict[str, str], container: Container
) -> None:
    container.steam = StaticSteamProvider({STEAM_ID: []})
    private = await client.post(
        "/api/v1/watchlist/import/steam", json={"profile": STEAM_ID}, headers=auth
    )
    assert private.status_code == 422 and "private" in private.json()["error"]["message"]
    missing = await client.post(
        "/api/v1/watchlist/import/steam", json={"profile": "nobody_here"}, headers=auth
    )
    assert missing.status_code == 422 and "Steam profile" in missing.json()["error"]["message"]
    unauth = await client.post("/api/v1/watchlist/import/steam", json={"profile": STEAM_ID})
    assert unauth.status_code == 401


async def test_import_respects_the_per_import_limit(
    client: httpx.AsyncClient, auth: dict[str, str], container: Container
) -> None:
    container.settings.wishlist_import_max = 1
    container.steam = StaticSteamProvider({STEAM_ID: [900003, 900006, 900004]})
    body = (
        await client.post(
            "/api/v1/watchlist/import/steam", json={"profile": STEAM_ID}, headers=auth
        )
    ).json()
    assert (body["added"], body["skipped_over_limit"]) == (1, 2)


# --- sale calendar ----------------------------------------------------------------


def test_calendar_follows_the_usual_schedule_from_2026() -> None:
    w = {x.kind: x for x in seasonal_windows(2026)}
    assert (w["SPRING"].start, w["SPRING"].end) == (date(2026, 3, 19), date(2026, 3, 26))
    assert (w["SUMMER"].start, w["SUMMER"].end) == (date(2026, 6, 25), date(2026, 7, 9))
    assert (w["AUTUMN"].start, w["AUTUMN"].end) == (date(2026, 10, 1), date(2026, 10, 8))
    assert (w["WINTER"].start, w["WINTER"].end) == (date(2026, 12, 17), date(2027, 1, 4))
    nxt = {x.kind: x for x in seasonal_windows(2027)}
    assert nxt["SPRING"].start == date(2027, 3, 18) and nxt["SUMMER"].start == date(2027, 6, 24)


def test_past_years_keep_the_dates_that_actually_happened() -> None:
    assert {x.kind: x.start for x in seasonal_windows(2025)}["AUTUMN"] == date(2025, 9, 29)
    assert {x.kind: x.start for x in seasonal_windows(2024)}["AUTUMN"] == date(2024, 11, 26)
    assert {x.kind: x.end for x in seasonal_windows(2025)}["WINTER"] == date(2026, 1, 1)


def test_next_fest_is_estimated_three_times_a_year_and_is_not_a_sale() -> None:
    fests = next_fests(2026)
    assert [f.start.month for f in fests] == [2, 6, 10]
    assert all(f.kind == "NEXT_FEST" for f in fests)
    assert "NEXT_FEST" not in {x.kind for x in seasonal_windows(2026)}
