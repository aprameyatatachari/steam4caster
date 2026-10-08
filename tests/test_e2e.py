"""End-to-end backend workflow:

register -> search -> add to watchlist -> ingest history -> forecast -> recommendation
-> price drop triggers alerts -> fake delivery recorded -> replays create no duplicates.
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy import func, select

from app.core.container import Container, RecordingDispatcher
from app.core.timeutil import utcnow
from app.models import Forecast, NotificationOutbox, PriceObservation, SaleEvent
from app.tasks import jobs
from tests.conftest import email_provider, register
from tests.helpers import ScriptedProvider, history_point, price_points, regular_sales, to_history


async def count(container: Container, model: type) -> int:
    async with container.db.session() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_full_backend_workflow(client: httpx.AsyncClient, container: Container) -> None:
    provider = ScriptedProvider()
    # Six past sales, 60 days apart at 50% off; the last one started 30 days ago.
    provider.series["US"] = to_history(price_points(utcnow(), sales=regular_sales(6, 60, 30)))
    container.price_provider = provider
    assert isinstance(container.tasks, RecordingDispatcher)

    # 1. Register and search.
    auth = await register(client, "player@example.com")
    game = (
        await client.get("/api/v1/games/search", params={"q": "scripted"}, headers=auth)
    ).json()[0]

    # 2. Add to the watchlist with alert preferences.
    created = await client.post(
        "/api/v1/watchlist",
        json={"game_id": game["id"], "target_price_minor": 1200, "min_discount_pct": 40,
              "max_wait_days": 45, "channels": ["EMAIL"]},
        headers=auth,
    )  # fmt: skip
    assert created.status_code == 201, created.text
    entry_id = created.json()["id"]
    assert container.tasks.calls[-1] == ("prices.process_series", (game["id"], "US"))

    # 3. The queued job ingests history, forecasts and evaluates alerts.
    email_provider(container).sent.clear()
    first = await jobs.process_series(container, game["id"], "US")
    assert first["alerts_created"] == 0  # full price: nothing to alert on
    assert await count(container, PriceObservation) == 13
    assert await count(container, SaleEvent) == 6

    # 4. Forecast and recommendation are available without any trained model.
    forecast = (await client.get(f"/api/v1/games/{game['id']}/forecast", headers=auth)).json()
    assert forecast["id"] == first["forecast_id"] and forecast["method"] == "BASELINE"
    assert forecast["sale_probability"]["days_30"] > 0.6
    assert forecast["likely_window"] is not None
    assert forecast["discount_tier_probabilities"]["50_TO_59"] > 0.5
    entry = (await client.get(f"/api/v1/watchlist/{entry_id}", headers=auth)).json()
    assert entry["recommendation"]["action"] == "WAIT"
    assert entry["recommendation"]["max_wait_days"] == 45
    assert {"HIGH_SALE_PROBABILITY", "MEANINGFUL_EXPECTED_SAVINGS"} <= set(
        entry["recommendation"]["reason_codes"]
    )

    # 5. A sale starts upstream. The scheduled refresh notices the change...
    provider.series["US"].append(history_point(utcnow(), cut=50))
    refreshed = await jobs.refresh_watched_prices(container)
    assert refreshed["changed"] == 1
    assert container.tasks.calls[-1] == ("prices.process_series", (game["id"], "US"))

    # ...and the follow-up job forecasts again and raises alerts.
    second = await jobs.process_series(container, game["id"], "US")
    assert second["forecast_id"] != first["forecast_id"]  # a new immutable forecast
    assert second["alerts_created"] == 3
    async with container.db.session() as session:
        old = await session.get(Forecast, uuid.UUID(forecast["id"]))
        assert old is not None and old.sale_prob_30d == forecast["sale_probability"]["days_30"]

    # 6. Delivery through the outbox.
    assert await jobs.dispatch_outbox(container) == {"SENT": 3}
    sent = email_provider(container).sent
    assert len(sent) == 3 and {m.email for m in sent} == {"player@example.com"}
    assert all(m.url == "https://example.invalid/steam/app/4242/?affiliate=keep-me" for m in sent)
    notifications = (await client.get("/api/v1/notifications", headers=auth)).json()["items"]
    assert {n["event_type"] for n in notifications} == {
        "TARGET_PRICE", "MIN_DISCOUNT", "BUY_TRANSITION",
    }  # fmt: skip
    assert {n["state"] for n in notifications} == {"SENT"}
    target = next(n for n in notifications if n["event_type"] == "TARGET_PRICE")
    assert "USD 10.00" in target["body"] and "USD 12.00" in target["body"]
    rec = (await client.get(f"/api/v1/watchlist/{entry_id}", headers=auth)).json()["recommendation"]
    assert rec["action"] == "BUY" and "NEAR_HISTORICAL_LOW" in rec["reason_codes"]

    # 7. Replays (at-least-once execution) create no duplicate observations or alerts.
    observations, forecasts = (
        await count(container, PriceObservation),
        await count(container, Forecast),
    )
    for _ in range(2):
        await jobs.refresh_watched_prices(container)
        replay = await jobs.process_series(container, game["id"], "US")
        assert replay["alerts_created"] == 0 and replay["forecast_id"] == second["forecast_id"]
        assert await jobs.dispatch_outbox(container) == {}
    assert await count(container, PriceObservation) == observations
    assert await count(container, Forecast) == forecasts
    assert await count(container, NotificationOutbox) == 3
    assert len(email_provider(container).sent) == 3

    # 8. A deeper discount is a new regional historical low: one new alert, while the
    #    still-true target/discount conditions are held back by the cooldown.
    provider.series["US"].append(history_point(utcnow(), cut=60))
    await jobs.refresh_watched_prices(container)
    third = await jobs.process_series(container, game["id"], "US")
    assert third["alerts_created"] == 3
    async with container.db.session() as session:
        rows = (
            await session.execute(
                select(NotificationOutbox.state, NotificationOutbox.error_code)
                .order_by(NotificationOutbox.created_at.desc())
                .limit(3)
            )
        ).all()
    assert sorted((r[0], r[1]) for r in rows) == [
        ("PENDING", None), ("SUPPRESSED", "COOLDOWN"), ("SUPPRESSED", "COOLDOWN"),
    ]  # fmt: skip
    assert await jobs.dispatch_outbox(container) == {"SENT": 1}
    assert "new lowest recorded price" in email_provider(container).sent[-1].title

    # 9. The summary reflects the live sale price in the region's own currency.
    summary = (await client.get("/api/v1/watchlist/summary", headers=auth)).json()
    assert summary["totals"][0]["current_total"] == {
        "amount_minor": 800, "currency": "USD", "amount": "8.00"
    }  # fmt: skip
    assert summary["recommendation_counts"]["BUY"] == 1


async def test_historical_low_only_entry_ignores_other_triggers(
    client: httpx.AsyncClient, container: Container
) -> None:
    provider = ScriptedProvider()
    provider.series["US"] = to_history(price_points(utcnow(), sales=regular_sales(6, 60, 30)))
    container.price_provider = provider
    auth = await register(client, "lowonly@example.com")
    game = (
        await client.get("/api/v1/games/search", params={"q": "scripted"}, headers=auth)
    ).json()[0]
    await client.post(
        "/api/v1/watchlist",
        json={"game_id": game["id"], "target_price_minor": 1900, "min_discount_pct": 10,
              "historical_low_only": True, "channels": ["EMAIL"]},
        headers=auth,
    )  # fmt: skip
    await jobs.process_series(container, game["id"], "US")

    provider.series["US"].append(history_point(utcnow(), cut=50))  # matches the old low only
    await jobs.refresh_watched_prices(container)
    assert (await jobs.process_series(container, game["id"], "US"))["alerts_created"] == 0

    provider.series["US"].append(history_point(utcnow(), cut=75))  # a genuinely new low
    await jobs.refresh_watched_prices(container)
    assert (await jobs.process_series(container, game["id"], "US"))["alerts_created"] == 1
    items = (await client.get("/api/v1/notifications", headers=auth)).json()["items"]
    assert [n["event_type"] for n in items] == ["HISTORICAL_LOW"]


async def test_many_watchers_share_one_provider_refresh(
    client: httpx.AsyncClient, container: Container
) -> None:
    provider = ScriptedProvider()
    provider.series["US"] = to_history(price_points(utcnow(), sales=regular_sales(4, 60, 30)))
    container.price_provider = provider
    game_id = None
    for i in range(4):
        auth = await register(client, f"watcher{i}@example.com")
        game_id = (
            await client.get("/api/v1/games/search", params={"q": "scripted"}, headers=auth)
        ).json()[0]["id"]
        await client.post("/api/v1/watchlist", json={"game_id": game_id}, headers=auth)
    before = provider.calls["prices"]
    result = await jobs.refresh_watched_prices(container)
    assert result["series"] == 1  # four users, one game/region
    assert provider.calls["prices"] == before + 1
