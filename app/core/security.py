"""Password hashing, JWT access tokens, opaque refresh tokens, and field encryption."""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings

_hasher = PasswordHasher()  # Argon2id with library defaults


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        # Burn comparable time so unknown accounts are not distinguishable by latency.
        _hasher.hash(password)
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


class TokenError(Exception):
    pass


def _encode(settings: Settings, claims: dict[str, Any], ttl: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        **claims,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "aud": settings.access_token_audience,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def _decode(settings: Settings, token: str, purpose: str) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.access_token_audience,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc
    if claims.get("typ") != purpose:
        raise TokenError("wrong token type")
    return claims


def create_access_token(settings: Settings, user_id: uuid.UUID) -> str:
    return _encode(
        settings,
        {"sub": str(user_id), "typ": "access"},
        timedelta(minutes=settings.jwt_access_ttl_minutes),
    )


def decode_access_token(settings: Settings, token: str) -> uuid.UUID:
    claims = _decode(settings, token, "access")
    try:
        return uuid.UUID(str(claims["sub"]))
    except ValueError as exc:
        raise TokenError("invalid subject") from exc


def create_purpose_token(
    settings: Settings, user_id: uuid.UUID, purpose: str, ttl: timedelta, **extra: Any
) -> str:
    return _encode(settings, {"sub": str(user_id), "typ": purpose, **extra}, ttl)


def decode_purpose_token(settings: Settings, token: str, purpose: str) -> dict[str, Any]:
    return _decode(settings, token, purpose)


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Stable SHA-256 digest for high-entropy opaque tokens (refresh tokens, fingerprints)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class FieldCipher:
    """Application-level encryption for sensitive columns (push subscription keys)."""

    def __init__(self, secret: str) -> None:
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("unable to decrypt value with the configured key") from exc


def generate_vapid_keypair() -> tuple[str, str]:
    """Return (public_key, private_key) as unpadded base64url strings for Web Push."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private = ec.generate_private_key(ec.SECP256R1())
    private_raw = private.private_numbers().private_value.to_bytes(32, "big")
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    return b64(public_raw), b64(private_raw)
