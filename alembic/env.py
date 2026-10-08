"""Alembic environment (async engine).

The URL is taken, in order, from ``-x url=...``, the ``sqlalchemy.url`` config option
(used by tests) and finally the application's DATABASE_URL setting.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  (registers every table on Base.metadata)
from alembic import context
from app.core.config import get_settings
from app.db.base import Base
from app.db.types import UTCDateTime

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    return (
        context.get_x_argument(as_dictionary=True).get("url")
        or config.get_main_option("sqlalchemy.url")
        or get_settings().database_url
    )


def render_item(type_: str, obj: Any, autogen_context: Any) -> str | bool:
    """Render application type decorators as plain SQLAlchemy types so migrations do
    not import application code."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def configure(connection: Connection | None = None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        render_item=render_item,
        compare_type=True,
        literal_binds=connection is None,
        dialect_opts={"paramstyle": "named"},
    )


def run_migrations_offline() -> None:
    configure(url=database_url())
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(database_url(), poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
