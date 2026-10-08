"""Standards-based Web Push (RFC 8030/8291/8292) using VAPID via ``pywebpush``."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.logging import get_logger, mask_endpoint
from app.models.enums import Channel
from app.providers.notifications.base import DeliveryResult, NotificationMessage

logger = get_logger(__name__)

MAX_PAYLOAD_BYTES = 3000  # push services cap encrypted payloads at roughly 4 KB
TTL_SECONDS = 24 * 3600


def classify_push_status(status: int | None) -> DeliveryResult:
    """Map a push-service HTTP status to a delivery outcome."""
    if status is None:
        return DeliveryResult.retry("PUSH_NETWORK_ERROR")
    if 200 <= status < 300:
        return DeliveryResult.ok()
    if status in (404, 410):
        # The subscription no longer exists; it must be removed, never retried.
        return DeliveryResult.fail(f"PUSH_GONE_{status}", destination_gone=True)
    if status in (408, 429) or status >= 500:
        return DeliveryResult.retry(f"PUSH_HTTP_{status}")
    return DeliveryResult.fail(f"PUSH_HTTP_{status}")


def build_payload(message: NotificationMessage) -> str:
    payload: dict[str, Any] = {"title": message.title[:120], "body": message.body, "data": {}}
    if message.url:
        payload["data"]["url"] = message.url
    if message.tag:
        payload["tag"] = message.tag
    payload["data"].update(message.data)
    encoded = json.dumps(payload, ensure_ascii=False)
    while len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES and len(payload["body"]) > 20:
        payload["body"] = payload["body"][: len(payload["body"]) * 3 // 4].rstrip() + "…"
        encoded = json.dumps(payload, ensure_ascii=False)
    return encoded


class WebPushProvider:
    channel = Channel.WEB_PUSH

    def __init__(self, vapid_private_key: str, vapid_subject: str, timeout: float = 10.0) -> None:
        self._private_key = vapid_private_key
        self._subject = vapid_subject
        self._timeout = timeout

    async def aclose(self) -> None:
        return None

    def _send_sync(self, message: NotificationMessage) -> DeliveryResult:
        from pywebpush import WebPushException, webpush

        assert message.push is not None
        try:
            response = webpush(
                subscription_info={
                    "endpoint": message.push.endpoint,
                    "keys": {"p256dh": message.push.p256dh, "auth": message.push.auth},
                },
                data=build_payload(message),
                vapid_private_key=self._private_key,
                vapid_claims={"sub": self._subject},
                ttl=TTL_SECONDS,
                timeout=self._timeout,
            )
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            return classify_push_status(status)
        except Exception as exc:
            # Connection errors and malformed subscription keys; never log the exception
            # text because it can contain the endpoint.
            name = type(exc).__name__
            if "Timeout" in name or "Connection" in name:
                return DeliveryResult.retry("PUSH_NETWORK_ERROR")
            return DeliveryResult.fail("PUSH_INVALID_SUBSCRIPTION")
        status = getattr(response, "status_code", 201)
        result = classify_push_status(status)
        location = getattr(response, "headers", {}).get("Location") if result.success else None
        # The Location header is itself a capability URL: keep only a short suffix.
        message_id = location.rsplit("/", 1)[-1][-32:] if location else None
        return DeliveryResult.ok(message_id) if result.success else result

    async def send(self, message: NotificationMessage) -> DeliveryResult:
        if message.push is None:
            return DeliveryResult.fail("PUSH_NO_SUBSCRIPTION")
        result = await asyncio.to_thread(self._send_sync, message)
        if not result.success:
            logger.info(
                "web push not delivered",
                extra={
                    "push_host": mask_endpoint(message.push.endpoint),
                    "code": result.error_code,
                },
            )
        return result
