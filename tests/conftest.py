from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from app.core.config import Settings
from app.core.container import Container, build_container
from app.db.base import Base
from app.main import create_app
from app.models.enums import Channel
from app.providers.notifications.base import FakeNotificationProvider

TEST_PASSWORD = "correct-horse-battery-staple"


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        # Fast in-memory SQLite by default; set TEST_DATABASE_URL to run on PostgreSQL.
        "database_url": os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite://"),
        "kv_backend": "memory",
        "price_provider": "fake",
        "email_provider": "fake",
        "tasks_disabled": True,
        "log_json": False,
        "log_level": "WARNING",
        "jwt_secret_key": "test-secret-key-that-is-long-enough-0123456789",
        "model_artifact_path": str(tmp_path / "artifacts"),
        "cors_allowed_origins": "http://localhost:3000",
        "default_country": "US",
        "default_currency": "USD",
        "rate_limit_auth_per_minute": 1000,
        "rate_limit_search_per_minute": 1000,
        "rate_limit_test_notification_per_hour": 2,
        "vapid_public_key": "BPublicKeyForTests",
        "vapid_private_key": "private-key-for-tests",
        "vapid_subject": "mailto:test@example.com",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest_asyncio.fixture
async def container(settings: Settings) -> AsyncIterator[Container]:
    built = build_container(settings)
    # Never talk to a real push service in tests.
    built.notifiers[Channel.WEB_PUSH] = FakeNotificationProvider(Channel.WEB_PUSH)
    async with built.db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield built
    finally:
        await built.aclose()


@pytest_asyncio.fixture
async def client(container: Container) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(container=container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def register(
    client: httpx.AsyncClient, email: str = "user@example.com", country: str = "US"
) -> dict[str, str]:
    """Register a user and return ready-to-use Authorization headers."""
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": TEST_PASSWORD, "country": country},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


@pytest_asyncio.fixture
async def auth(client: httpx.AsyncClient) -> dict[str, str]:
    return await register(client)


def email_provider(container: Container) -> FakeNotificationProvider:
    provider = container.notifiers[Channel.EMAIL]
    assert isinstance(provider, FakeNotificationProvider)
    return provider


def push_provider(container: Container) -> FakeNotificationProvider:
    provider = container.notifiers[Channel.WEB_PUSH]
    assert isinstance(provider, FakeNotificationProvider)
    return provider
