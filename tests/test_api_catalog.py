from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.core.container import Container
from app.core.timeutil import utcnow
from app.models import Forecast, Game, PriceObservation, SaleEvent
from app.providers.pricing.base import ProviderRateLimited, ProviderUnavailable
from app.services.catalog import CatalogService
from app.services.prices import PriceService
from tests.conftest import register
from tests.helpers import ScriptedProvider, price_points, regular_sales, to_history


async def find_game(client: httpx.AsyncClient, auth: dict[str, str], q: str = "Hollow") -> dict:
    response = await client.get("/api/v1/games/search", params={"q": q}, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()[0]


async def test_search_upserts_and_returns_internal_and_external_ids(
    client: httpx.AsyncClient, auth: dict[str, str], container: Container
) -> None:
    first = await client.get("/api/v1/games/search", params={"q": "starfall"}, headers=auth)
    assert first.status_code == 200
    titles = [g["title"] for g in first.json()]
    assert titles[0] == "Starfall Tactics" and len(titles) == 3
    again = await client.get("/api/v1/games/search", params={"q": "starfall"}, headers=auth)
    assert [g["id"] for g in again.json()] == [g["id"] for g in first.json()]  # stable ids
    assert all(g["itad_id"] for g in first.json())
    async with container.db.session() as session:
        assert await session.scalar(select(func.count()).select_from(Game)) == 3
    short = await client.get("/api/v1/games/search", params={"q": "s"}, headers=auth)
    assert short.status_code == 422


async def test_lookup_by_steam_app_id_and_metadata(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.get(
        "/api/v1/games/lookup", params={"steam_app_id": 900003}, headers=auth
    )
    assert response.status_code == 200
    game = response.json()
    assert game["steam_app_id"] == 900003 and game["itad_id"] != str(game["steam_app_id"])
    assert game["publishers"] == ["Emberlight"] and game["release_date"] == "2018-06-05"
    detail = await client.get(f"/api/v1/games/{game['id']}", headers=auth)
    assert detail.json()["tags"] == ["Metroidvania", "Indie"]
    assert (
        await client.get("/api/v1/games/lookup", params={"steam_app_id": 1}, headers=auth)
    ).status_code == 404
    assert (await client.get("/api/v1/games/lookup", headers=auth)).status_code == 422


async def test_prices_are_regional_and_never_converted(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    game = await find_game(client, auth)
    us = (
        await client.get(
            f"/api/v1/games/{game['id']}/prices", params={"country": "US"}, headers=auth
        )
    ).json()
    india = (
        await client.get(
            f"/api/v1/games/{game['id']}/prices", params={"country": "in"}, headers=auth
        )
    ).json()
    assert (us["currency"], india["currency"]) == ("USD", "INR")
    assert us["regular"] == {"amount_minor": 1999, "currency": "USD", "amount": "19.99"}
    assert india["regular"] == {"amount_minor": 67900, "currency": "INR", "amount": "679.00"}
    # The INR price is its own observation, not the USD price times an exchange rate.
    assert india["regular"]["amount_minor"] / us["regular"]["amount_minor"] != pytest.approx(
        83, rel=0.2
    )
    assert india["historical_low"]["currency"] == "INR"
    assert india["attribution"]["provider"] == "IsThereAnyDeal"
    assert india["url"].startswith("https://example.invalid/fake/steam/app/")
    assert india["is_stale"] is False


async def test_unsupported_shop_and_region_without_price(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    game = await find_game(client, auth)
    gog = await client.get(
        f"/api/v1/games/{game['id']}/prices", params={"shop": "gog"}, headers=auth
    )
    assert gog.status_code == 422 and gog.json()["error"]["details"] == {
        "supported_shops": ["steam"]
    }
    nowhere = await client.get(
        f"/api/v1/games/{game['id']}/prices", params={"country": "ZZ"}, headers=auth
    )
    assert nowhere.status_code == 404
    bad = await client.get(
        f"/api/v1/games/{game['id']}/prices", params={"country": "USA"}, headers=auth
    )
    assert bad.status_code == 422
    missing = await client.get(
        "/api/v1/games/00000000-0000-0000-0000-000000000000/prices", headers=auth
    )
    assert missing.status_code == 404


async def test_history_is_cursor_paginated_and_chronological(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    game = await find_game(client, auth)
    url = f"/api/v1/games/{game['id']}/history"
    collected: list[dict] = []
    cursor: str | None = None
    pages = 0
    while True:
        params = {"country": "US", "limit": 7, **({"cursor": cursor} if cursor else {})}
        page = (await client.get(url, params=params, headers=auth)).json()
        collected.extend(page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    stamps = [item["observed_at"] for item in collected]
    assert pages > 2 and stamps == sorted(stamps) and len(set(stamps)) == len(stamps)
    assert all(item["price"]["currency"] == "USD" for item in collected)
    everything = (
        await client.get(url, params={"country": "US", "limit": 1000}, headers=auth)
    ).json()
    assert everything["next_cursor"] is None and len(everything["items"]) == len(collected)

    windowed = (
        await client.get(
            url, params={"country": "US", "from": "2024-01-01T00:00:00Z", "to": "2024-12-31T00:00:00Z"},
            headers=auth,
        )
    ).json()  # fmt: skip
    assert windowed["items"] and all(i["observed_at"].startswith("2024") for i in windowed["items"])
    naive = await client.get(url, params={"from": "2024-01-01T00:00:00"}, headers=auth)
    assert naive.status_code == 422
    assert (await client.get(url, params={"cursor": "garbage"}, headers=auth)).status_code == 422

    events = (
        await client.get(
            f"/api/v1/games/{game['id']}/sale-events", params={"country": "US"}, headers=auth
        )
    ).json()
    assert len(events) > 5 and all(e["max_discount_pct"] > 0 for e in events)


async def test_forecast_and_recommendation_endpoints(
    client: httpx.AsyncClient, auth: dict[str, str], container: Container
) -> None:
    game = await find_game(client, auth)
    response = await client.get(
        f"/api/v1/games/{game['id']}/forecast", params={"country": "IN"}, headers=auth
    )
    assert response.status_code == 200, response.text
    forecast = response.json()
    probs = forecast["sale_probability"]
    assert 0 <= probs["days_7"] <= probs["days_30"] <= probs["days_90"] <= 1
    assert forecast["method"] == "BASELINE" and forecast["model_version"] == "baseline-1"
    assert forecast["currency"] == "INR" and forecast["regular_price"]["currency"] == "INR"
    assert sum(forecast["discount_tier_probabilities"].values()) == pytest.approx(1.0, abs=1e-4)
    interval = forecast["predicted_sale_price"]
    assert interval["lower"]["amount_minor"] <= interval["median"]["amount_minor"]
    assert interval["median"]["amount_minor"] <= interval["upper"]["amount_minor"] <= 67900
    assert forecast["explanation_factors"] and forecast["data_quality"] == "GOOD"
    assert "feature_snapshot" not in forecast and "features" not in response.text
    assert "not guarantees" in forecast["disclaimer"]

    # A second read returns the same immutable forecast instead of creating another.
    again = (
        await client.get(
            f"/api/v1/games/{game['id']}/forecast", params={"country": "IN"}, headers=auth
        )
    ).json()
    assert again["id"] == forecast["id"]

    rec = await client.get(
        f"/api/v1/games/{game['id']}/recommendation",
        params={"country": "IN", "max_wait_days": 45},
        headers=auth,
    )
    assert rec.status_code == 200, rec.text
    body = rec.json()
    assert body["action"] in {"BUY", "WAIT", "NEUTRAL"} and body["reason_codes"]
    assert body["forecast_id"] == forecast["id"] and body["max_wait_days"] == 45
    assert body["ruleset_version"] == "rules-1" and body["is_estimate"] is True
    assert body["thresholds"]["wait_min_probability"] == 0.6

    history = (
        await client.get(
            f"/api/v1/games/{game['id']}/forecast-history", params={"country": "IN"}, headers=auth
        )
    ).json()
    assert [f["id"] for f in history["items"]] == [forecast["id"]]
    async with container.db.session() as session:
        assert await session.scalar(select(func.count()).select_from(Forecast)) == 1


async def test_cold_start_game_gets_low_confidence_forecast(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    game = await find_game(client, auth, "Brand New")
    forecast = (
        await client.get(
            f"/api/v1/games/{game['id']}/forecast", params={"country": "US"}, headers=auth
        )
    ).json()
    assert forecast["data_quality"] == "INSUFFICIENT"
    assert forecast["confidence_score"] <= 0.3
    assert forecast["likely_window"] is None
    assert "INSUFFICIENT_GAME_HISTORY" in {f["code"] for f in forecast["explanation_factors"]}
    rec = (
        await client.get(
            f"/api/v1/games/{game['id']}/recommendation", params={"country": "US"}, headers=auth
        )
    ).json()
    assert rec["action"] == "NEUTRAL" and rec["reason_codes"] == ["LOW_CONFIDENCE"]


async def test_search_is_rate_limited_per_user(
    client: httpx.AsyncClient, container: Container
) -> None:
    container.settings.rate_limit_search_per_minute = 2
    alice = await register(client, "alice@example.com")
    bob = await register(client, "bob@example.com")
    statuses = [
        (await client.get("/api/v1/games/search", params={"q": "star"}, headers=alice)).status_code
        for _ in range(3)
    ]
    assert statuses == [200, 200, 429]
    assert (
        await client.get("/api/v1/games/search", params={"q": "star"}, headers=bob)
    ).status_code == 200


# --- provider failures and ingestion ---------------------------------------


@pytest.fixture
def scripted(container: Container) -> ScriptedProvider:
    provider = ScriptedProvider()
    provider.series["US"] = to_history(price_points(utcnow(), sales=regular_sales(6, 60, 30)))
    container.price_provider = provider
    return provider


async def test_provider_outage_maps_to_503_without_leaking_details(
    client: httpx.AsyncClient, auth: dict[str, str], scripted: ScriptedProvider
) -> None:
    scripted.failures.append(ProviderUnavailable("connect to api.isthereanydeal.com?key=SECRET"))
    response = await client.get("/api/v1/games/search", params={"q": "script"}, headers=auth)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert "SECRET" not in response.text

    scripted.failures.append(ProviderRateLimited(retry_after=42))
    limited = await client.get("/api/v1/games/search", params={"q": "script"}, headers=auth)
    assert limited.status_code == 503 and limited.headers["Retry-After"] == "42"


async def test_search_falls_back_to_local_catalogue_when_provider_is_down(
    client: httpx.AsyncClient, auth: dict[str, str], scripted: ScriptedProvider
) -> None:
    assert (
        await client.get("/api/v1/games/search", params={"q": "script"}, headers=auth)
    ).status_code == 200
    scripted.failures.append(ProviderUnavailable("down"))
    degraded = await client.get("/api/v1/games/search", params={"q": "script"}, headers=auth)
    assert degraded.status_code == 200 and degraded.json()[0]["title"] == "Scripted Quest"


async def test_stale_price_is_served_when_refresh_fails(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    scripted: ScriptedProvider,
    container: Container,
) -> None:
    game = await find_game(client, auth, "script")
    url = f"/api/v1/games/{game['id']}/prices"
    assert (await client.get(url, headers=auth)).status_code == 200
    container.settings.price_stale_after_seconds = 0  # everything is now stale
    scripted.failures.append(ProviderUnavailable("down"))
    stale = await client.get(url, headers=auth)
    assert stale.status_code == 200 and stale.json()["is_stale"] is True


async def _game_and_shop(container: Container, session):  # type: ignore[no-untyped-def]
    catalog = CatalogService(session, container)
    game = (await catalog.search("script"))[0]
    return await catalog.ensure_info(game), await catalog.steam_shop()


async def test_history_ingestion_is_idempotent_and_incremental(
    container: Container, scripted: ScriptedProvider
) -> None:
    async with container.db.session() as session:
        game, shop = await _game_and_shop(container, session)
        prices = PriceService(session, container)
        first = await prices.ingest_history(game, shop, "US")
        assert first.inserted == 13 and first.sale_events == 6
        second = await prices.ingest_history(game, shop, "US", full=True)  # full replay
        assert second.inserted == 0 and second.sale_events == 6
        assert await session.scalar(select(func.count()).select_from(PriceObservation)) == 13
        assert await session.scalar(select(func.count()).select_from(SaleEvent)) == 6

        # A new sale appears upstream: the incremental read only adds the new point.
        scripted.series["US"].extend(to_history(price_points(utcnow(), sales=[(0, 0, 60)])[1:]))
        third = await prices.ingest_history(game, shop, "US")
        assert third.inserted == 1 and third.sale_events == 7
        events = await prices.list_sale_events(game, shop, "US")
        assert events[-1].ended_at is None and events[-1].max_discount_pct == 60


async def test_backfill_is_restartable_after_a_failure(
    container: Container, scripted: ScriptedProvider
) -> None:
    from app.repositories import prices as prices_repo

    async with container.db.session() as session:
        game, shop = await _game_and_shop(container, session)
        prices = PriceService(session, container)
        scripted.failures.append(ProviderUnavailable("boom"))
        with pytest.raises(ProviderUnavailable):
            await prices.ingest_history(game, shop, "US")
        watermark = await prices_repo.get_watermark(session, game.id, shop.id, "US")
        assert watermark is not None
        assert watermark.backfill_completed_at is None  # did not advance on failure
        assert watermark.last_error_code == "PROVIDER_UNAVAILABLE"

        result = await prices.ingest_history(game, shop, "US")
        assert result.inserted == 13
        await session.refresh(watermark)
        assert watermark.backfill_completed_at is not None and watermark.last_error_code is None
        since_calls = scripted.calls["history"]
        # Once backfilled, the next read is incremental (small overlap), not a full re-read.
        await prices.ingest_history(game, shop, "US")
        assert scripted.calls["history"] == since_calls + 1
        assert watermark.history_since is not None
        assert watermark.covered_until - watermark.history_since > timedelta(days=365)


async def test_currency_change_does_not_mix_currencies_in_a_series(
    container: Container, scripted: ScriptedProvider
) -> None:
    from app.repositories import prices as prices_repo

    now = utcnow()
    old = to_history(price_points(now - timedelta(days=400), sales=[(100, 7, 50)]), currency="ARS")
    scripted.series["AR"] = old + to_history(
        price_points(now, sales=[(60, 7, 25)], first_days_ago=300), currency="USD"
    )
    async with container.db.session() as session:
        game, shop = await _game_and_shop(container, session)
        await PriceService(session, container).ingest_history(game, shop, "AR")
        points, currency = await prices_repo.series_points(session, game.id, shop.id, "AR")
        assert currency == "USD"
        assert len(points) == 3  # the ARS era is excluded rather than compared
        events = await prices_repo.list_sale_events(session, game.id, shop.id, "AR")
        assert [e.currency for e in events] == ["USD"]
