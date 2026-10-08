"""Optional SMS channel (Twilio). Off unless ``SMS_ENABLED=true`` and fully configured.

SMS is never free: trial accounts only reach verified numbers and production traffic
carries per-message and carrier costs. See ``docs/operations.md`` for the compliance
notes (opt-in, STOP handling, sender registration).
"""

from __future__ import annotations

import httpx
import phonenumbers

from app.models.enums import Channel
from app.providers.notifications.base import DeliveryResult, NotificationMessage

SMS_MAX_CHARS = 300


def normalize_e164(number: str, default_region: str | None = None) -> str:
    """Validate a phone number and return it in E.164. Raises ``ValueError`` if invalid."""
    try:
        parsed = phonenumbers.parse(number, default_region)
    except phonenumbers.NumberParseException as exc:
        raise ValueError("Invalid phone number.") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("Invalid phone number.")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


class DisabledSmsProvider:
    """Stands in when SMS is switched off so the rest of the system needs no special case."""

    channel = Channel.SMS

    async def send(self, message: NotificationMessage) -> DeliveryResult:
        return DeliveryResult.fail("SMS_DISABLED")

    async def aclose(self) -> None:
        return None


class TwilioSmsProvider:
    channel = Channel.SMS

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        *,
        base_url: str = "https://api.twilio.com",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._account_sid = account_sid
        self._from = from_number
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(10.0), auth=(account_sid, auth_token)
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, message: NotificationMessage) -> DeliveryResult:
        if not message.phone:
            return DeliveryResult.fail("SMS_NO_RECIPIENT")
        text = f"{message.title}: {message.body}"
        if message.url:
            text = f"{text[: SMS_MAX_CHARS - len(message.url) - 1]} {message.url}"
        text = f"{text[:SMS_MAX_CHARS]} Reply STOP to opt out."
        try:
            response = await self._client.post(
                f"/2010-04-01/Accounts/{self._account_sid}/Messages.json",
                data={"To": message.phone, "From": self._from, "Body": text},
            )
        except (httpx.TimeoutException, httpx.TransportError):
            return DeliveryResult.retry("SMS_NETWORK_ERROR")
        status = response.status_code
        if 200 <= status < 300:
            try:
                sid = str(response.json().get("sid") or "") or None
            except ValueError:
                sid = None
            return DeliveryResult.ok(sid)
        if status == 429 or status >= 500:
            return DeliveryResult.retry(f"SMS_HTTP_{status}")
        return DeliveryResult.fail(f"SMS_HTTP_{status}")
