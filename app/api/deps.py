"""FastAPI dependencies: container, database session, current user and rate limits."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.core.errors import AuthenticationError, RateLimited
from app.core.kv import hit_rate_limit
from app.core.metrics import RATE_LIMIT_REJECTIONS
from app.core.security import TokenError, decode_access_token
from app.models import User

_bearer = HTTPBearer(auto_error=False, description="Access token from /api/v1/auth/login.")


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


async def get_session(
    container: Annotated[Container, Depends(get_container)],
) -> AsyncIterator[AsyncSession]:
    """One session per request. Services commit explicitly; anything left uncommitted
    when the request ends (including on errors) is rolled back."""
    async with container.db.session() as session:
        yield session


async def get_current_user(
    container: Annotated[Container, Depends(get_container)],
    session: Annotated[AsyncSession, Depends(get_session)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise AuthenticationError()
    try:
        user_id = decode_access_token(container.settings, credentials.credentials)
    except TokenError as exc:
        raise AuthenticationError("Invalid or expired access token.") from exc
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid or expired access token.")
    return user


ContainerDep = Annotated[Container, Depends(get_container)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit_by_ip(
    scope: str, setting: str, window_seconds: int
) -> Callable[..., Awaitable[None]]:
    """Fixed-window limit keyed by client IP (for unauthenticated endpoints)."""

    async def dependency(request: Request, container: ContainerDep) -> None:
        limit = int(getattr(container.settings, setting))
        if await hit_rate_limit(container.kv, scope, client_ip(request), window_seconds) > limit:
            RATE_LIMIT_REJECTIONS.labels(scope).inc()
            raise RateLimited(headers={"Retry-After": str(window_seconds)})

    return dependency


def rate_limit_by_user(
    scope: str, setting: str, window_seconds: int
) -> Callable[..., Awaitable[None]]:
    """Fixed-window limit keyed by the authenticated user."""

    async def dependency(user: CurrentUser, container: ContainerDep) -> None:
        limit = int(getattr(container.settings, setting))
        if await hit_rate_limit(container.kv, scope, str(user.id), window_seconds) > limit:
            RATE_LIMIT_REJECTIONS.labels(scope).inc()
            raise RateLimited(headers={"Retry-After": str(window_seconds)})

    return dependency
