"""Steam community lookups used for wishlist import.

Only public data is read: a profile's Steam ID (to resolve a custom profile URL) and the
app ids on a public wishlist. No Steam account is signed in and no API key is used.
"""

from __future__ import annotations

import re
from typing import Protocol

import httpx

_STEAM_ID = re.compile(r"^\d{17}$")
_PROFILE_URL = re.compile(r"steamcommunity\.com/profiles/(\d{17})", re.IGNORECASE)
_VANITY_URL = re.compile(r"steamcommunity\.com/id/([A-Za-z0-9_-]{2,64})", re.IGNORECASE)
_VANITY_NAME = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
_STEAM_ID_XML = re.compile(r"<steamID64>(\d{17})</steamID64>")


class SteamError(Exception):
    """Base class. ``code`` is safe to show to a user."""

    code = "STEAM_ERROR"


class SteamProfileNotFound(SteamError):
    code = "STEAM_PROFILE_NOT_FOUND"


class SteamUnavailable(SteamError):
    code = "STEAM_UNAVAILABLE"


def parse_profile_reference(value: str) -> tuple[str, str]:
    """Classify what the user pasted as ``("id", steam_id)`` or ``("vanity", name)``."""
    text = value.strip()
    if _STEAM_ID.match(text):
        return "id", text
    if match := _PROFILE_URL.search(text):
        return "id", match.group(1)
    if match := _VANITY_URL.search(text):
        return "vanity", match.group(1)
    if _VANITY_NAME.match(text):
        return "vanity", text
    raise SteamProfileNotFound("not a Steam profile link, custom URL name or 17-digit Steam ID")


class SteamProfileProvider(Protocol):
    async def resolve_steam_id(self, reference: str) -> str: ...

    async def get_wishlist_app_ids(self, steam_id: str) -> list[int]: ...

    async def aclose(self) -> None: ...


class SteamCommunityProvider:
    def __init__(
        self,
        *,
        user_agent: str = "Steam4Caster/0.1",
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout), headers={"User-Agent": user_agent}
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str, **params: str) -> httpx.Response:
        try:
            return await self._client.get(url, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise SteamUnavailable("Steam did not respond") from exc

    async def resolve_steam_id(self, reference: str) -> str:
        kind, value = parse_profile_reference(reference)
        if kind == "id":
            return value
        response = await self._get(f"https://steamcommunity.com/id/{value}/", xml="1")
        if response.status_code >= 500:
            raise SteamUnavailable(f"Steam returned HTTP {response.status_code}")
        match = _STEAM_ID_XML.search(response.text)
        if match is None:
            raise SteamProfileNotFound("no Steam profile has that custom URL")
        return match.group(1)

    async def get_wishlist_app_ids(self, steam_id: str) -> list[int]:
        """App ids on the wishlist in the owner's ranked order (unranked items last).

        A private or empty wishlist returns an empty list; Steam does not say which.
        """
        response = await self._get(
            "https://api.steampowered.com/IWishlistService/GetWishlist/v1/", steamid=steam_id
        )
        if response.status_code >= 500 or response.status_code == 429:
            raise SteamUnavailable(f"Steam returned HTTP {response.status_code}")
        if response.status_code != 200:
            raise SteamProfileNotFound("Steam rejected that Steam ID")
        try:
            items = response.json().get("response", {}).get("items", []) or []
            ranked = sorted(
                items,
                key=lambda item: (
                    int(item.get("priority") or 0) == 0,
                    int(item.get("priority") or 0),
                    int(item.get("date_added") or 0),
                ),
            )
            return [int(item["appid"]) for item in ranked]
        except (KeyError, TypeError, ValueError) as exc:
            raise SteamUnavailable("unexpected wishlist payload") from exc


class StaticSteamProvider:
    """Canned wishlists for tests."""

    def __init__(self, wishlists: dict[str, list[int]], vanities: dict[str, str] | None = None):
        self.wishlists = wishlists
        self.vanities = vanities or {}

    async def aclose(self) -> None:
        return None

    async def resolve_steam_id(self, reference: str) -> str:
        kind, value = parse_profile_reference(reference)
        if kind == "id":
            return value
        if value not in self.vanities:
            raise SteamProfileNotFound("no Steam profile has that custom URL")
        return self.vanities[value]

    async def get_wishlist_app_ids(self, steam_id: str) -> list[int]:
        return list(self.wishlists.get(steam_id, []))
