"""First-party authentication: Argon2id passwords, short-lived JWT access tokens and
rotating, revocable refresh tokens stored only as SHA-256 digests.

The rest of the application depends only on ``User`` rows and ``get_current_user``, so
an external identity provider can replace this module (``users.external_subject``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.core.errors import AuthenticationError, ConflictError, ValidationFailed
from app.core.logging import get_logger
from app.core.money import normalize_country, normalize_currency
from app.core.security import (
    TokenError,
    create_access_token,
    create_purpose_token,
    decode_purpose_token,
    hash_password,
    hash_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.core.timeutil import utcnow, validate_timezone
from app.models import RefreshToken, User
from app.models.enums import Channel
from app.providers.notifications.base import NotificationMessage

logger = get_logger(__name__)

EMAIL_VERIFY_PURPOSE = "email_verify"
EMAIL_VERIFY_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


def normalize_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(self, session: AsyncSession, container: Container) -> None:
        self.session = session
        self.container = container
        self.settings = container.settings

    async def _issue(self, user: User, family_id: uuid.UUID | None = None) -> TokenPair:
        refresh = new_refresh_token()
        self.session.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_token(refresh),
                family_id=family_id or uuid.uuid4(),
                expires_at=utcnow() + timedelta(days=self.settings.jwt_refresh_ttl_days),
            )
        )
        await self.session.commit()
        return TokenPair(
            access_token=create_access_token(self.settings, user.id),
            refresh_token=refresh,
            expires_in=self.settings.jwt_access_ttl_minutes * 60,
        )

    async def register(
        self,
        *,
        email: str,
        password: str,
        country: str | None,
        currency: str | None,
        timezone: str | None,
    ) -> tuple[User, TokenPair]:
        try:
            user = User(
                email=normalize_email(email),
                password_hash=hash_password(password),
                default_country=normalize_country(country or self.settings.default_country),
                default_currency=normalize_currency(currency or self.settings.default_currency),
                timezone=validate_timezone(timezone or "UTC"),
            )
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        self.session.add(user)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("An account with this email already exists.") from exc
        tokens = await self._issue(user)
        await self.send_verification_email(user)
        return user, tokens

    async def login(self, *, email: str, password: str) -> tuple[User, TokenPair]:
        user = (
            await self.session.scalars(select(User).where(User.email == normalize_email(email)))
        ).first()
        valid = verify_password(password, user.password_hash if user else None)
        if user is None or not valid or not user.is_active:
            raise AuthenticationError("Invalid email or password.")
        if user.password_hash and password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        return user, await self._issue(user)

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Rotate a refresh token. Presenting an already-rotated token is treated as
        theft and revokes every token in its family."""
        row = (
            await self.session.scalars(
                select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token))
            )
        ).first()
        now = utcnow()
        if row is None:
            raise AuthenticationError("Invalid refresh token.")
        if row.rotated_at is not None or row.revoked_at is not None:
            await self._revoke_family(row.family_id)
            await self.session.commit()
            logger.warning("refresh token reuse detected", extra={"user_id": str(row.user_id)})
            raise AuthenticationError("Invalid refresh token.")
        if row.expires_at <= now:
            raise AuthenticationError("Refresh token expired.")
        user = await self.session.get(User, row.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid refresh token.")
        # Compare-and-set so two concurrent refreshes cannot both succeed.
        claimed = await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == row.id, RefreshToken.rotated_at.is_(None))
            .values(rotated_at=now)
        )
        if claimed.rowcount != 1:  # type: ignore[attr-defined]
            await self._revoke_family(row.family_id)
            await self.session.commit()
            raise AuthenticationError("Invalid refresh token.")
        return await self._issue(user, row.family_id)

    async def _revoke_family(self, family_id: uuid.UUID) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )

    async def logout(self, refresh_token: str) -> None:
        row = (
            await self.session.scalars(
                select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token))
            )
        ).first()
        if row is not None:
            await self._revoke_family(row.family_id)
            await self.session.commit()

    async def send_verification_email(self, user: User) -> bool:
        """Security email: sent regardless of the user's optional alert preferences."""
        provider = self.container.notifiers.get(Channel.EMAIL)
        if provider is None or user.email_verified:
            return False
        token = create_purpose_token(self.settings, user.id, EMAIL_VERIFY_PURPOSE, EMAIL_VERIFY_TTL)
        link = f"{self.settings.frontend_base_url.rstrip('/')}/verify-email?token={token}"
        try:
            result = await provider.send(
                NotificationMessage(
                    channel=Channel.EMAIL,
                    title=f"Verify your {self.settings.app_name} email address",
                    body="Confirm this email address to receive price alerts by email. "
                    "The link is valid for 24 hours.",
                    url=link,
                    email=user.email,
                )
            )
        except Exception:
            logger.exception("verification email failed")
            return False
        return result.success

    async def verify_email(self, token: str) -> User:
        try:
            claims = decode_purpose_token(self.settings, token, EMAIL_VERIFY_PURPOSE)
            user_id = uuid.UUID(str(claims["sub"]))
        except (TokenError, ValueError) as exc:
            raise ValidationFailed("The verification link is invalid or has expired.") from exc
        user = await self.session.get(User, user_id)
        if user is None:
            raise ValidationFailed("The verification link is invalid or has expired.")
        user.email_verified = True
        await self.session.commit()
        return user

    async def update_profile(
        self, user: User, *, country: str | None, currency: str | None, timezone: str | None
    ) -> User:
        try:
            if country is not None:
                user.default_country = normalize_country(country)
            if currency is not None:
                user.default_currency = normalize_currency(currency)
            if timezone is not None:
                user.timezone = validate_timezone(timezone)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        await self.session.commit()
        return user
