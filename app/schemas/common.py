from __future__ import annotations

from typing import Annotated, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.money import format_minor

T = TypeVar("T")

CountryCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z]{2}$")
]
CurrencyCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z]{3}$")
]

ESTIMATE_NOTICE = (
    "Forecasts and recommendations are statistical estimates based on past prices. "
    "They are not guarantees and not financial advice."
)


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, protected_namespaces=())


class Money(ApiModel):
    """An amount in integer minor units with its ISO 4217 currency. ``amount`` is a
    decimal string for display; clients must not parse it into binary floats."""

    amount_minor: int = Field(examples=[149900])
    currency: str = Field(examples=["INR"])
    amount: str = Field(examples=["1499.00"])


def money(amount_minor: int | None, currency: str | None) -> Money | None:
    if amount_minor is None or not currency:
        return None
    return Money(
        amount_minor=amount_minor, currency=currency, amount=format_minor(amount_minor, currency)
    )


class Page[T](ApiModel):
    items: list[T]
    next_cursor: str | None = Field(
        default=None, description="Pass as `cursor` to fetch the next page; null on the last page."
    )


class Attribution(ApiModel):
    provider: str
    text: str
    url: str
    affiliation: str
