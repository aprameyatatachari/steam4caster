"""Async engine/session management and dialect-portable idempotent inserts."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool


class Database:
    def __init__(self, url: str, *, pooled: bool = True, echo: bool = False) -> None:
        kwargs: dict[str, Any] = {"echo": echo}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url or url.endswith("://"):
                kwargs["poolclass"] = StaticPool
        elif pooled:
            kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=10)
        else:
            # Celery tasks run each job in a fresh event loop; connections must not
            # outlive the loop that created them.
            kwargs["poolclass"] = NullPool
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine.sync_engine, "connect")
            def _fk_pragma(dbapi_connection: Any, _: Any) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            try:
                yield session
            except BaseException:
                await session.rollback()
                raise

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def dispose(self) -> None:
        await self.engine.dispose()


async def insert_ignore(
    session: AsyncSession,
    table: Any,
    rows: Sequence[dict[str, Any]],
    conflict_columns: Sequence[str],
    *,
    chunk_size: int = 500,
) -> int:
    """INSERT ... ON CONFLICT DO NOTHING. Returns the number of rows actually inserted."""
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    inserted = 0
    for start in range(0, len(rows), chunk_size):
        chunk = list(rows[start : start + chunk_size])
        stmt = insert(table).values(chunk).on_conflict_do_nothing(index_elements=conflict_columns)
        result = await session.execute(stmt)
        inserted += max(result.rowcount or 0, 0)  # type: ignore[attr-defined]
    return inserted


async def upsert(
    session: AsyncSession,
    table: Any,
    row: dict[str, Any],
    conflict_columns: Sequence[str],
    update_columns: Sequence[str],
) -> None:
    """INSERT ... ON CONFLICT DO UPDATE for a single row."""
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert(table).values(row)
    stmt = stmt.on_conflict_do_update(
        index_elements=conflict_columns,
        set_={col: getattr(stmt.excluded, col) for col in update_columns},
    )
    await session.execute(stmt)
