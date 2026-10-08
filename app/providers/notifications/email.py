"""Email providers: Resend (HTTP API) and SMTP (Mailpit locally, any relay in production)."""

from __future__ import annotations

import asyncio
import html
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

import httpx

from app.models.enums import Channel
from app.providers.notifications.base import DeliveryResult, NotificationMessage


def render_text(message: NotificationMessage) -> str:
    parts = [message.body]
    if message.url:
        parts.append(message.url)
    parts.append(
        "Estimates are statistical, not guarantees. Price data provided by the "
        "IsThereAnyDeal API. Manage alerts in your notification settings."
    )
    return "\n\n".join(parts)


def render_html(message: NotificationMessage) -> str:
    link = (
        f'<p><a href="{html.escape(message.url, quote=True)}">View the game</a></p>'
        if message.url
        else ""
    )
    return (
        f"<h2>{html.escape(message.title)}</h2><p>{html.escape(message.body)}</p>{link}"
        '<p style="color:#666;font-size:12px">Estimates are statistical, not guarantees. '
        "Price data provided by the IsThereAnyDeal API. Manage alerts in your notification "
        "settings.</p>"
    )


class ResendEmailProvider:
    channel = Channel.EMAIL

    def __init__(
        self,
        api_key: str,
        sender: str,
        *,
        base_url: str = "https://api.resend.com",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._sender = sender
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(10.0),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, message: NotificationMessage) -> DeliveryResult:
        if not message.email:
            return DeliveryResult.fail("EMAIL_NO_RECIPIENT")
        headers = {}
        if message.idempotency_key:
            # Resend de-duplicates on this header, covering a crash between send and commit.
            headers["Idempotency-Key"] = message.idempotency_key
        try:
            response = await self._client.post(
                "/emails",
                headers=headers,
                json={
                    "from": self._sender,
                    "to": [message.email],
                    "subject": message.title,
                    "text": render_text(message),
                    "html": render_html(message),
                },
            )
        except (httpx.TimeoutException, httpx.TransportError):
            return DeliveryResult.retry("EMAIL_NETWORK_ERROR")
        status = response.status_code
        if 200 <= status < 300:
            try:
                message_id = str(response.json().get("id") or "") or None
            except ValueError:
                message_id = None
            return DeliveryResult.ok(message_id)
        if status in (408, 429) or status >= 500:
            return DeliveryResult.retry(f"EMAIL_HTTP_{status}")
        return DeliveryResult.fail(f"EMAIL_HTTP_{status}")


class SmtpEmailProvider:
    channel = Channel.EMAIL

    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        *,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = False,
        timeout: float = 10.0,
    ) -> None:
        self._host, self._port, self._sender = host, port, sender
        self._username, self._password = username, password
        self._starttls, self._timeout = starttls, timeout

    async def aclose(self) -> None:
        return None

    def _send_sync(self, message: NotificationMessage) -> DeliveryResult:
        assert message.email is not None
        email = EmailMessage()
        email["From"] = self._sender
        email["To"] = message.email
        email["Subject"] = message.title
        email["Message-ID"] = make_msgid(domain="steam4caster.local")
        email.set_content(render_text(message))
        email.add_alternative(render_html(message), subtype="html")
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
                if self._starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if self._username and self._password:
                    smtp.login(self._username, self._password)
                smtp.send_message(email)
        except smtplib.SMTPRecipientsRefused:
            return DeliveryResult.fail("EMAIL_RECIPIENT_REFUSED")
        except smtplib.SMTPAuthenticationError:
            return DeliveryResult.fail("EMAIL_AUTH_FAILED")
        except smtplib.SMTPResponseException as exc:
            if 400 <= exc.smtp_code < 500:
                return DeliveryResult.retry(f"EMAIL_SMTP_{exc.smtp_code}")
            return DeliveryResult.fail(f"EMAIL_SMTP_{exc.smtp_code}")
        except (OSError, smtplib.SMTPException):
            return DeliveryResult.retry("EMAIL_NETWORK_ERROR")
        return DeliveryResult.ok(str(email["Message-ID"]).strip("<>"))

    async def send(self, message: NotificationMessage) -> DeliveryResult:
        if not message.email:
            return DeliveryResult.fail("EMAIL_NO_RECIPIENT")
        return await asyncio.to_thread(self._send_sync, message)
