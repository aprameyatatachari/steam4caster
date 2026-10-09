"""Price-data provider boundary.

External payloads are converted to these internal schemas inside each provider;
nothing outside ``app.providers.pricing`` sees a provider's wire format.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol

from pydantic import BaseModel, Field

ATTRIBUTION = {
    "provider": "IsThereAnyDeal",
    "text": "Price data provided by the IsThereAnyDeal API.",
    "url": "https://isthereanydeal.com/",
    "affiliation": "Steam4Caster is not affiliated with or endorsed by IsThereAnyDeal or Valve.",
}


class ProviderError(Exception):
    """Base class for provider failures. ``transient`` drives retry decisions."""

    transient = False
    code = "PROVIDER_ERROR"


class ProviderUnavailable(ProviderError):
    transient = True
    code = "PROVIDER_UNAVAILABLE"


class ProviderRateLimited(ProviderError):
    transient = True
    code = "PROVIDER_RATE_LIMITED"

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("provider rate limit reached")
        self.retry_after = retry_after


class ProviderAuthError(ProviderError):
    code = "PROVIDER_AUTH_ERROR"


class ProviderBadResponse(ProviderError):
    code = "PROVIDER_BAD_RESPONSE"


class Money(BaseModel):
    amount_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ProviderGame(BaseModel):
    provider_id: str
    slug: str
    title: str
    type: str | None = None
    mature: bool = False
    assets: dict[str, str] = Field(default_factory=dict)


class ProviderGameInfo(ProviderGame):
    steam_app_id: int | None = None
    early_access: bool | None = None
    release_date: date | None = None
    developers: list[str] = Field(default_factory=list)
    publishers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    url: str | None = None


class ProviderShop(BaseModel):
    provider_shop_id: int
    name: str


class ProviderDeal(BaseModel):
    shop_id: int
    shop_name: str
    price: Money
    regular: Money
    cut: int = Field(ge=0, le=100)
    store_low: Money | None = None
    timestamp: datetime
    expiry: datetime | None = None
    url: str | None = None


class ProviderGamePrices(BaseModel):
    provider_id: str
    deals: list[ProviderDeal] = Field(default_factory=list)


class ProviderHistoryPoint(BaseModel):
    timestamp: datetime
    shop_id: int
    price: Money
    regular: Money
    cut: int = Field(ge=0, le=100)


class PriceDataProvider(Protocol):
    name: str

    async def search_games(self, title: str, limit: int = 20) -> list[ProviderGame]: ...

    async def lookup_game(
        self, *, title: str | None = None, steam_app_id: int | None = None
    ) -> ProviderGame | None: ...

    async def get_game_info(self, provider_id: str) -> ProviderGameInfo | None: ...

    async def get_current_prices(
        self, provider_ids: Sequence[str], country: str, shop_ids: Sequence[int]
    ) -> list[ProviderGamePrices]: ...

    async def get_price_history(
        self, provider_id: str, country: str, shop_ids: Sequence[int], since: datetime
    ) -> list[ProviderHistoryPoint]: ...

    async def list_shops(self, country: str) -> list[ProviderShop]: ...

    async def list_popular_games(self, limit: int = 100, offset: int = 0) -> list[ProviderGame]: ...

    async def aclose(self) -> None: ...
