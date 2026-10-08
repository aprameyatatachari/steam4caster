from __future__ import annotations

import httpx
from sqlalchemy import select

from app.core.container import Container
from app.core.security import create_purpose_token
from app.models import RefreshToken, User
from app.services.auth import EMAIL_VERIFY_PURPOSE, EMAIL_VERIFY_TTL
from tests.conftest import TEST_PASSWORD, email_provider, register


async def test_register_login_me_flow(client: httpx.AsyncClient, container: Container) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "  Player@Example.COM ", "password": TEST_PASSWORD, "country": "in",
              "currency": "inr", "timezone": "Asia/Kolkata"},
    )  # fmt: skip
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == "player@example.com"  # normalised
    assert body["user"]["default_country"] == "IN" and body["user"]["default_currency"] == "INR"
    assert "password" not in response.text and "password_hash" not in response.text
    assert body["tokens"]["token_type"] == "bearer" and body["tokens"]["expires_in"] == 900

    login = await client.post(
        "/api/v1/auth/login", json={"email": "player@example.com", "password": TEST_PASSWORD}
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}
    me = await client.get("/api/v1/me", headers=headers)
    assert me.status_code == 200 and me.json()["timezone"] == "Asia/Kolkata"

    async with container.db.session() as session:
        user = (await session.scalars(select(User))).one()
        assert user.password_hash and user.password_hash.startswith("$argon2id$")
        tokens = list(await session.scalars(select(RefreshToken)))
        raw = {body["tokens"]["refresh_token"], login.json()["tokens"]["refresh_token"]}
        assert all(t.token_hash not in raw and len(t.token_hash) == 64 for t in tokens)


async def test_duplicate_email_conflicts(client: httpx.AsyncClient) -> None:
    await register(client, "dup@example.com")
    response = await client.post(
        "/api/v1/auth/register", json={"email": "DUP@example.com", "password": TEST_PASSWORD}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


async def test_validation_errors_use_the_error_envelope(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": "short"},
        headers={"X-Request-ID": "corr-id-12345"},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["correlation_id"] == "corr-id-12345"
    assert response.headers["X-Request-ID"] == "corr-id-12345"
    assert {tuple(d["loc"])[-1] for d in error["details"]} == {"email", "password"}
    bad_tz = await client.post(
        "/api/v1/auth/register",
        json={"email": "tz@example.com", "password": TEST_PASSWORD, "timezone": "Mars/Olympus"},
    )
    assert bad_tz.status_code == 422


async def test_wrong_password_and_unknown_user_are_indistinguishable(
    client: httpx.AsyncClient,
) -> None:
    await register(client, "known@example.com")
    wrong = await client.post(
        "/api/v1/auth/login", json={"email": "known@example.com", "password": "wrong-password"}
    )
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_protected_routes_require_a_valid_access_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/me")).status_code == 401
    bad = await client.get("/api/v1/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert bad.status_code == 401
    assert bad.headers["WWW-Authenticate"] == "Bearer"
    assert bad.json()["error"]["code"] == "UNAUTHENTICATED"
    for path in ("/api/v1/watchlist", "/api/v1/games/search?q=star", "/api/v1/notifications",
                 "/api/v1/model-performance/summary"):  # fmt: skip
        assert (await client.get(path)).status_code == 401, path


async def test_refresh_rotates_and_reuse_revokes_the_family(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/v1/auth/register", json={"email": "rot@example.com", "password": TEST_PASSWORD}
    )
    first = created.json()["tokens"]["refresh_token"]
    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert rotated.status_code == 200
    second = rotated.json()["refresh_token"]
    assert second != first

    # Replaying the already-rotated token is treated as theft...
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert replay.status_code == 401
    # ...and revokes the legitimate descendant too.
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": second})
    ).status_code == 401


async def test_logout_revokes_the_refresh_token(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/v1/auth/register", json={"email": "out@example.com", "password": TEST_PASSWORD}
    )
    token = created.json()["tokens"]["refresh_token"]
    assert (
        await client.post("/api/v1/auth/logout", json={"refresh_token": token})
    ).status_code == 204
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
    ).status_code == 401


async def test_auth_endpoints_are_rate_limited(
    client: httpx.AsyncClient, container: Container
) -> None:
    container.settings.rate_limit_auth_per_minute = 3
    statuses = [
        (
            await client.post(
                "/api/v1/auth/login", json={"email": "x@example.com", "password": "whatever-pass"}
            )
        ).status_code
        for _ in range(5)
    ]
    assert statuses == [401, 401, 401, 429, 429]


async def test_email_verification_is_a_security_email(
    client: httpx.AsyncClient, container: Container
) -> None:
    headers = await register(client, "verify@example.com")
    sent = email_provider(container).sent
    assert len(sent) == 1 and "Verify" in sent[0].title and sent[0].email == "verify@example.com"
    assert (await client.get("/api/v1/me", headers=headers)).json()["email_verified"] is False

    async with container.db.session() as session:
        user = (await session.scalars(select(User))).one()
    token = create_purpose_token(
        container.settings, user.id, EMAIL_VERIFY_PURPOSE, EMAIL_VERIFY_TTL
    )
    confirmed = await client.post("/api/v1/auth/verify-email/confirm", json={"token": token})
    assert confirmed.status_code == 200 and confirmed.json()["email_verified"] is True

    # An access token is not accepted as a verification token.
    access = headers["Authorization"].split()[1]
    assert (
        await client.post("/api/v1/auth/verify-email/confirm", json={"token": access})
    ).status_code == 422


async def test_cors_allows_only_listed_origins(client: httpx.AsyncClient) -> None:
    preflight = {"Access-Control-Request-Method": "GET"}
    allowed = await client.options(
        "/api/v1/me", headers={"Origin": "http://localhost:3000", **preflight}
    )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    denied = await client.options(
        "/api/v1/me", headers={"Origin": "https://evil.example", **preflight}
    )
    assert "access-control-allow-origin" not in denied.headers
    assert allowed.headers["access-control-allow-origin"] != "*"
