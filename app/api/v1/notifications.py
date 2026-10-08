from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import ContainerDep, CurrentUser, SessionDep
from app.core.container import Container
from app.core.pagination import decode_cursor, encode_cursor
from app.models import NotificationPreference
from app.models.enums import Channel
from app.schemas.notifications import (
    NotificationOut,
    NotificationPage,
    PreferenceOut,
    PreferencesOut,
    PreferencesPatch,
    PushSubscriptionCreate,
    PushSubscriptionOut,
    SmsConfirmRequest,
    SmsVerifyRequest,
    TestNotificationOut,
    TestNotificationResult,
)
from app.services.notifications import NotificationService

router = APIRouter(tags=["notifications"])


def _available(container: Container, channel: Channel) -> bool:
    if channel == Channel.SMS:
        return container.settings.sms_configured
    return channel in container.notifiers


def _mask(value: str | None) -> str | None:
    return f"{'•' * max(0, len(value) - 4)}{value[-4:]}" if value else None


def _preferences(rows: list[NotificationPreference], container: Container) -> PreferencesOut:
    return PreferencesOut(
        preferences=[
            PreferenceOut(
                channel=p.channel,
                enabled=p.enabled,
                available=_available(container, Channel(p.channel)),
                quiet_hours_start=p.quiet_hours_start,
                quiet_hours_end=p.quiet_hours_end,
                timezone=p.timezone,
                delivery_mode=p.delivery_mode,
                destination_masked=_mask(p.destination),
                destination_verified=p.destination_verified,
            )
            for p in rows
        ],
        vapid_public_key=container.settings.vapid_public_key
        if container.settings.web_push_configured
        else None,
    )


@router.get(
    "/notification-preferences",
    response_model=PreferencesOut,
    summary="Notification preferences per channel",
)
async def get_preferences(
    user: CurrentUser, session: SessionDep, container: ContainerDep
) -> PreferencesOut:
    rows = await NotificationService(session, container).get_preferences(user)
    return _preferences(rows, container)


@router.patch(
    "/notification-preferences",
    response_model=PreferencesOut,
    summary="Update notification preferences",
    description="Fields present in each item are applied. Quiet hours are interpreted in "
    "the item's IANA timezone; alerts raised during quiet hours are delivered afterwards.",
)
async def patch_preferences(
    body: PreferencesPatch, user: CurrentUser, session: SessionDep, container: ContainerDep
) -> PreferencesOut:
    updates = []
    for item in body.preferences:
        data = item.model_dump(exclude_unset=True)
        data["channel"] = item.channel.value
        if item.delivery_mode is not None:
            data["delivery_mode"] = item.delivery_mode.value
        updates.append(data)
    rows = await NotificationService(session, container).update_preferences(user, updates)
    return _preferences(rows, container)


@router.post(
    "/notification-preferences/sms/verify",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send an SMS verification code (only when SMS is enabled on the server)",
)
async def sms_verify(
    body: SmsVerifyRequest, user: CurrentUser, session: SessionDep, container: ContainerDep
) -> dict[str, bool]:
    await NotificationService(session, container).request_sms_verification(user, body.phone)
    return {"sent": True}


@router.post(
    "/notification-preferences/sms/confirm",
    response_model=PreferencesOut,
    summary="Confirm the SMS verification code (records explicit opt-in)",
)
async def sms_confirm(
    body: SmsConfirmRequest, user: CurrentUser, session: SessionDep, container: ContainerDep
) -> PreferencesOut:
    service = NotificationService(session, container)
    await service.confirm_sms_verification(user, body.code)
    return _preferences(await service.get_preferences(user), container)


@router.get(
    "/push-subscriptions",
    response_model=list[PushSubscriptionOut],
    summary="Active Web Push subscriptions",
)
async def list_push_subscriptions(
    user: CurrentUser, session: SessionDep, container: ContainerDep
) -> list[PushSubscriptionOut]:
    rows = await NotificationService(session, container).list_push_subscriptions(user)
    return [PushSubscriptionOut.model_validate(r) for r in rows]


@router.post(
    "/push-subscriptions",
    response_model=PushSubscriptionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a browser Web Push subscription",
)
async def create_push_subscription(
    body: PushSubscriptionCreate, user: CurrentUser, session: SessionDep, container: ContainerDep
) -> PushSubscriptionOut:
    row = await NotificationService(session, container).register_push_subscription(
        user,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        device_label=body.device_label,
        expires_at=body.expiration_time,
    )
    return PushSubscriptionOut.model_validate(row)


@router.delete(
    "/push-subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a Web Push subscription",
)
async def delete_push_subscription(
    subscription_id: uuid.UUID, user: CurrentUser, session: SessionDep, container: ContainerDep
) -> Response:
    await NotificationService(session, container).revoke_push_subscription(user, subscription_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/notifications/test",
    response_model=TestNotificationOut,
    summary="Send a test notification to every usable channel (strictly rate limited)",
)
async def send_test(
    user: CurrentUser, session: SessionDep, container: ContainerDep
) -> TestNotificationOut:
    rows = await NotificationService(session, container).send_test(user)
    return TestNotificationOut(
        results=[
            TestNotificationResult(channel=r.channel, state=r.state, error_code=r.error_code)
            for r in rows
        ]
    )


@router.get(
    "/notifications",
    response_model=NotificationPage,
    summary="In-app notification history (newest first)",
)
async def list_notifications(
    user: CurrentUser,
    session: SessionDep,
    container: ContainerDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> NotificationPage:
    rows = await NotificationService(session, container).history(
        user, before=decode_cursor(cursor) if cursor else None, limit=limit + 1
    )
    page, more = rows[:limit], len(rows) > limit
    return NotificationPage(
        items=[
            NotificationOut(
                id=outbox.id,
                event_type=event.event_type,
                channel=outbox.channel,
                state=outbox.state,
                title=event.title,
                body=event.body,
                url=event.url,
                game_id=event.game_id,
                watchlist_entry_id=event.watchlist_entry_id,
                error_code=outbox.error_code,
                attempt_count=outbox.attempt_count,
                created_at=outbox.created_at,
                sent_at=outbox.sent_at,
                payload=event.payload,
            )
            for outbox, event in page
        ],
        next_cursor=encode_cursor(page[-1][0].created_at, page[-1][0].id) if more else None,
    )
