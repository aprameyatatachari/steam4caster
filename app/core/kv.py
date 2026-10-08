"""Small key-value abstraction over Redis (cache, locks, rate-limit counters).

Redis is never treated as durable storage. ``MemoryKV`` is a per-process stand-in
for tests and single-process development.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from app.core.logging import get_logger

logger = get_logger(__name__)


class KeyValueStore(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def incr(self, key: str, ttl: int) -> int: ...
    async def set_nx(self, key: str, value: str, ttl: int) -> bool: ...
    async def delete_if_equals(self, key: str, value: str) -> bool: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


class MemoryKV:
    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float | None]] = {}

    def _live(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires <= time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    async def get(self, key: str) -> str | None:
        return self._live(key)

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        self._data[key] = (value, time.monotonic() + ttl if ttl else None)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def incr(self, key: str, ttl: int) -> int:
        current = self._live(key)
        if current is None:
            self._data[key] = ("1", time.monotonic() + ttl)
            return 1
        value = int(current) + 1
        self._data[key] = (str(value), self._data[key][1])
        return value

    async def set_nx(self, key: str, value: str, ttl: int) -> bool:
        if self._live(key) is not None:
            return False
        self._data[key] = (value, time.monotonic() + ttl)
        return True

    async def delete_if_equals(self, key: str, value: str) -> bool:
        if self._live(key) == value:
            self._data.pop(key, None)
            return True
        return False

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._data.clear()


_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end
"""


class RedisKV:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self._redis = redis.from_url(
            url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3
        )

    async def get(self, key: str) -> str | None:
        value = await self._redis.get(key)
        return None if value is None else str(value)

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        await self._redis.set(key, value, ex=ttl)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def incr(self, key: str, ttl: int) -> int:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl, nx=True)
            value, _ = await pipe.execute()
        return int(value)

    async def set_nx(self, key: str, value: str, ttl: int) -> bool:
        return bool(await self._redis.set(key, value, ex=ttl, nx=True))

    async def delete_if_equals(self, key: str, value: str) -> bool:
        return bool(await self._redis.eval(_RELEASE_SCRIPT, 1, key, value))

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self._redis.aclose()


class ResilientKV:
    """Wraps a store so cache/lock outages degrade instead of failing reads.

    Cache reads miss, writes are dropped, and counters report 0 (rate limiting fails
    open) while the backend is unavailable. Lock acquisition fails closed.
    """

    def __init__(self, inner: KeyValueStore) -> None:
        self._inner = inner

    async def get(self, key: str) -> str | None:
        try:
            return await self._inner.get(key)
        except Exception:
            logger.warning("kv get failed", extra={"kv_key_prefix": key.split(":")[0]})
            return None

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        try:
            await self._inner.set(key, value, ttl)
        except Exception:
            logger.warning("kv set failed", extra={"kv_key_prefix": key.split(":")[0]})

    async def delete(self, key: str) -> None:
        try:
            await self._inner.delete(key)
        except Exception:
            logger.warning("kv delete failed", extra={"kv_key_prefix": key.split(":")[0]})

    async def incr(self, key: str, ttl: int) -> int:
        try:
            return await self._inner.incr(key, ttl)
        except Exception:
            logger.warning("kv incr failed", extra={"kv_key_prefix": key.split(":")[0]})
            return 0

    async def set_nx(self, key: str, value: str, ttl: int) -> bool:
        try:
            return await self._inner.set_nx(key, value, ttl)
        except Exception:
            logger.warning("kv set_nx failed", extra={"kv_key_prefix": key.split(":")[0]})
            return False

    async def delete_if_equals(self, key: str, value: str) -> bool:
        try:
            return await self._inner.delete_if_equals(key, value)
        except Exception:
            return False

    async def ping(self) -> bool:
        try:
            return await self._inner.ping()
        except Exception:
            return False

    async def close(self) -> None:
        try:
            await self._inner.close()
        except Exception:
            logger.warning("kv close failed")


@asynccontextmanager
async def distributed_lock(kv: KeyValueStore, name: str, ttl: int = 300) -> AsyncIterator[bool]:
    """Best-effort distributed lock. Yields whether the lock was acquired.

    Callers must skip their work when it yields ``False``; work guarded by the lock
    must additionally be idempotent because the lock can expire.
    """
    token = secrets.token_hex(16)
    key = f"lock:{name}"
    acquired = await kv.set_nx(key, token, ttl)
    try:
        yield acquired
    finally:
        if acquired:
            await kv.delete_if_equals(key, token)


async def hit_rate_limit(kv: KeyValueStore, scope: str, identity: str, window_seconds: int) -> int:
    """Increment and return the fixed-window counter for ``identity`` in ``scope``."""
    bucket = int(time.time() // window_seconds)
    return await kv.incr(f"rl:{scope}:{identity}:{bucket}", window_seconds)
