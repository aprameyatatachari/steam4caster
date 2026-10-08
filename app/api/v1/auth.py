from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import ContainerDep, CurrentUser, SessionDep, rate_limit_by_ip
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    ProfileUpdate,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
    VerifyEmailRequest,
)
from app.services.auth import AuthService, TokenPair

router = APIRouter(tags=["auth"])
_auth_limit = Depends(rate_limit_by_ip("auth", "rate_limit_auth_per_minute", 60))


def _tokens(pair: TokenPair) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post(
    "/auth/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_auth_limit],
    summary="Create an account",
)
async def register(
    body: RegisterRequest, session: SessionDep, container: ContainerDep
) -> AuthResponse:
    user, pair = await AuthService(session, container).register(
        email=body.email,
        password=body.password,
        country=body.country,
        currency=body.currency,
        timezone=body.timezone,
    )
    return AuthResponse(user=UserOut.model_validate(user), tokens=_tokens(pair))


@router.post(
    "/auth/login", response_model=AuthResponse, dependencies=[_auth_limit], summary="Sign in"
)
async def login(body: LoginRequest, session: SessionDep, container: ContainerDep) -> AuthResponse:
    user, pair = await AuthService(session, container).login(
        email=body.email, password=body.password
    )
    return AuthResponse(user=UserOut.model_validate(user), tokens=_tokens(pair))


@router.post(
    "/auth/refresh",
    response_model=TokenResponse,
    dependencies=[_auth_limit],
    summary="Rotate a refresh token",
    description="Returns a new access/refresh pair. The presented refresh token becomes "
    "invalid; presenting it again revokes the whole session.",
)
async def refresh(
    body: RefreshRequest, session: SessionDep, container: ContainerDep
) -> TokenResponse:
    return _tokens(await AuthService(session, container).refresh(body.refresh_token))


@router.post(
    "/auth/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke a refresh token"
)
async def logout(body: RefreshRequest, session: SessionDep, container: ContainerDep) -> Response:
    await AuthService(session, container).logout(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/auth/verify-email/request",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[_auth_limit],
    summary="Send a new email verification link",
)
async def request_verification(
    user: CurrentUser, session: SessionDep, container: ContainerDep
) -> dict[str, bool]:
    return {"sent": await AuthService(session, container).send_verification_email(user)}


@router.post(
    "/auth/verify-email/confirm",
    response_model=UserOut,
    dependencies=[_auth_limit],
    summary="Confirm an email address with the emailed token",
)
async def confirm_verification(
    body: VerifyEmailRequest, session: SessionDep, container: ContainerDep
) -> UserOut:
    return UserOut.model_validate(await AuthService(session, container).verify_email(body.token))


@router.get("/me", response_model=UserOut, summary="Current user")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me", response_model=UserOut, summary="Update default region and timezone")
async def update_me(
    body: ProfileUpdate, user: CurrentUser, session: SessionDep, container: ContainerDep
) -> UserOut:
    updated = await AuthService(session, container).update_profile(
        user,
        country=body.default_country,
        currency=body.default_currency,
        timezone=body.timezone,
    )
    return UserOut.model_validate(updated)
