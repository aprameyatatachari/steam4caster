"""Migrations must build exactly the schema the models declare, and be reversible.

Always exercised on SQLite. The PostgreSQL run uses ``TEST_DATABASE_URL`` when set (CI),
otherwise a Testcontainers instance when Docker is available, otherwise it is skipped.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.db.base import Base

EXPECTED_TABLES = {
    "users", "refresh_tokens", "games", "shops", "regional_game_prices", "price_observations",
    "sale_events", "ingestion_watermarks", "watchlist_entries", "notification_preferences",
    "push_subscriptions", "forecasts", "recommendations", "model_versions",
    "notification_events", "notification_outbox", "notification_deliveries",
}  # fmt: skip


def alembic_config(url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    return config


def inspect_database(url: str) -> tuple[set[str], list[object], dict[str, set[str]]]:
    async def run() -> tuple[set[str], list[object], dict[str, set[str]]]:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.connect() as connection:

            def sync(conn):  # type: ignore[no-untyped-def]
                inspector = inspect(conn)
                tables = set(inspector.get_table_names()) - {"alembic_version"}
                diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
                uniques = {
                    table: {u["name"] for u in inspector.get_unique_constraints(table) if u["name"]}
                    | {i["name"] for i in inspector.get_indexes(table) if i["unique"] and i["name"]}
                    for table in tables
                }
                return tables, diff, uniques

            result = await connection.run_sync(sync)
        await engine.dispose()
        return result

    return asyncio.run(run())


def check_migrations(url: str) -> None:
    config = alembic_config(url)
    command.upgrade(config, "head")
    tables, diff, uniques = inspect_database(url)
    assert tables == EXPECTED_TABLES
    assert diff == [], f"models and migrations have drifted: {diff}"
    # The constraints that make ingestion and alerting replay-safe must exist.
    assert "uq_price_observations_identity" in uniques["price_observations"]
    assert "uq_regional_game_prices_identity" in uniques["regional_game_prices"]
    assert "uq_notification_outbox_idempotency_key" in uniques["notification_outbox"]
    assert "uq_watchlist_entries_user_game" in uniques["watchlist_entries"]
    assert "uq_users_email" in uniques["users"]

    command.downgrade(config, "base")
    assert inspect_database(url)[0] == set()
    command.upgrade(config, "head")  # and back up again cleanly
    assert inspect_database(url)[0] == EXPECTED_TABLES


def test_migrations_on_sqlite(tmp_path: Path) -> None:
    check_migrations(f"sqlite+aiosqlite:///{(tmp_path / 'migrations.db').as_posix()}")


@pytest.fixture
def postgres_url() -> Iterator[str]:
    configured = os.environ.get("TEST_DATABASE_URL", "")
    if configured.startswith("postgresql"):
        # Start from an empty schema: other tests create tables without Alembic.
        async def reset() -> None:
            engine = create_async_engine(configured, poolclass=NullPool)
            async with engine.begin() as connection:
                await connection.execute(text("DROP SCHEMA public CASCADE"))
                await connection.execute(text("CREATE SCHEMA public"))
            await engine.dispose()

        asyncio.run(reset())
        yield configured
        asyncio.run(reset())
        return
    try:
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer("postgres:16-alpine", driver="asyncpg")
        container.start()
    except Exception as exc:
        pytest.skip(f"PostgreSQL is not available (no TEST_DATABASE_URL and no Docker): {exc}")
    try:
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.mark.postgres
def test_migrations_on_postgresql(postgres_url: str) -> None:
    check_migrations(postgres_url)

    # PostgreSQL-specific expectations: JSONB columns and enforced CHECK constraints.
    async def run() -> None:
        engine = create_async_engine(postgres_url, poolclass=NullPool)
        async with engine.connect() as connection:
            kind = await connection.scalar(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'forecasts' AND column_name = 'feature_snapshot'"
                )
            )
            assert kind == "jsonb"
            with pytest.raises(Exception, match="discount_range"):
                await connection.execute(
                    text(
                        "INSERT INTO shops (provider_shop_id, name, slug, is_active, updated_at) "
                        "VALUES (1, 'Steam', 'steam', true, now())"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO games (id, title, slug, mature, developers, publishers, tags, "
                        "assets, created_at, updated_at) VALUES "
                        "('00000000-0000-0000-0000-000000000001', 't', 't', false, '[]', '[]', "
                        "'[]', '{}', now(), now())"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO price_observations (game_id, shop_id, country, currency, "
                        "price_minor, regular_minor, discount_pct, observed_at, ingested_at, source) "
                        "SELECT '00000000-0000-0000-0000-000000000001', id, 'US', 'USD', 1, 1, 150, "
                        "now(), now(), 'history' FROM shops"
                    )
                )
        await engine.dispose()

    asyncio.run(run())
