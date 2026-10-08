from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import ConfigDict, EmailStr, Field

from app.schemas.common import ApiModel, CountryCode, CurrencyCode


class RegisterRequest(ApiModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "player@example.com",
                "password": "correct-horse-battery-staple",
                "country": "IN",
                "currency": "INR",
                "timezone": "Asia/Kolkata",
            }
        }
    )

    email: EmailStr
    password: str = Field(min_length=10, max_length=256)
    country: CountryCode | None = None
    currency: CurrencyCode | None = None
    timezone: str | None = Field(default=None, max_length=64, description="IANA zone name.")


class LoginRequest(ApiModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(ApiModel):
    refresh_token: str = Field(min_length=20, max_length=512)


class VerifyEmailRequest(ApiModel):
    token: str = Field(min_length=20, max_length=2048)


class TokenResponse(ApiModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int = Field(description="Access-token lifetime in seconds.")


class UserOut(ApiModel):
    id: uuid.UUID
    email: EmailStr
    default_country: str
    default_currency: str
    timezone: str
    email_verified: bool
    created_at: datetime


class AuthResponse(ApiModel):
    user: UserOut
    tokens: TokenResponse


class ProfileUpdate(ApiModel):
    default_country: CountryCode | None = None
    default_currency: CurrencyCode | None = None
    timezone: str | None = Field(default=None, max_length=64)
