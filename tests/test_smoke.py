from __future__ import annotations

import httpx

from app.core.container import Container


async def test_health_endpoints(client: httpx.AsyncClient) -> None:
    live = await client.get("/health/live")
    assert live.status_code == 200 and live.json() == {"status": "ok"}
    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "ready"
    assert body["components"]["database"] == "ok"
    # Readiness never echoes secrets.
    assert "test-secret-key" not in ready.text


async def test_readiness_fails_without_database(
    client: httpx.AsyncClient, container: Container
) -> None:
    async def down() -> bool:
        return False

    container.db.ping = down  # type: ignore[method-assign]
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["components"]["database"] == "unavailable"


async def test_cache_outage_only_degrades_readiness(
    client: httpx.AsyncClient, container: Container
) -> None:
    async def down() -> bool:
        return False

    container.kv.ping = down  # type: ignore[method-assign]
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


async def test_openapi_document_is_generated(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    paths = schema["paths"]
    for path in (
        "/api/v1/auth/register",
        "/api/v1/games/search",
        "/api/v1/games/{game_id}/forecast",
        "/api/v1/watchlist/summary",
        "/api/v1/notification-preferences",
        "/api/v1/model-performance/calibration",
        "/api/v1/model-versions/active",
    ):
        assert path in paths, path
    assert "example" in schema["components"]["schemas"]["ForecastOut"]


async def test_meta_exposes_attribution_and_public_key_only(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/meta")).json()
    assert body["attribution"]["provider"] == "IsThereAnyDeal"
    assert body["vapid_public_key"] == "BPublicKeyForTests"
    assert "private-key-for-tests" not in str(body)
    assert [t["code"] for t in body["discount_tiers"]][0] == "LT_20"


async def test_metrics_use_route_templates_not_raw_paths(client: httpx.AsyncClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    await client.get(f"/api/v1/games/{missing}")
    await client.get("/health/live")
    text = (await client.get("/metrics")).text
    assert 'route="/api/v1/games/{game_id}"' in text
    assert 'route="/health/live"' in text
    assert missing not in text  # ids never become metric labels
