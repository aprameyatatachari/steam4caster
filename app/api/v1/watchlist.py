from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import ContainerDep, CurrentUser, SessionDep
from app.schemas.watchlist import (
    LikelySale,
    WatchlistCreate,
    WatchlistEntryOut,
    WatchlistSummary,
    WatchlistUpdate,
    currency_totals,
    entry_out,
)
from app.services.watchlist import WatchlistService

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistEntryOut], summary="List the user's watchlist")
async def list_watchlist(
    user: CurrentUser, session: SessionDep, container: ContainerDep
) -> list[WatchlistEntryOut]:
    views = await WatchlistService(session, container).list_entries(user)
    return [entry_out(v) for v in views]


@router.post(
    "",
    response_model=WatchlistEntryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a game to the watchlist",
)
async def create_entry(
    body: WatchlistCreate, user: CurrentUser, session: SessionDep, container: ContainerDep
) -> WatchlistEntryOut:
    view = await WatchlistService(session, container).create(
        user,
        game_id=body.game_id,
        country=body.country,
        currency=body.currency,
        target_price_minor=body.target_price_minor,
        min_discount_pct=body.min_discount_pct,
        max_wait_days=body.max_wait_days,
        historical_low_only=body.historical_low_only,
        notify_on_buy=body.notify_on_buy,
        channels=[c.value for c in body.channels] if body.channels is not None else None,
    )
    return entry_out(view)


@router.get(
    "/summary",
    response_model=WatchlistSummary,
    summary="Portfolio-style totals and recommendation counts",
    description="Current cost, cost at recorded historical lows and modelled expected cost "
    "per horizon, grouped by currency. Modelled values are estimates.",
)
async def summary(
    user: CurrentUser, session: SessionDep, container: ContainerDep
) -> WatchlistSummary:
    data = await WatchlistService(session, container).summary(user)
    return WatchlistSummary(
        entries=data["entries"],
        totals=[currency_totals(t) for t in data["totals"]],
        recommendation_counts=data["recommendation_counts"],
        likely_on_sale_within_30d=[
            LikelySale(**item) for item in data["likely_on_sale_within_30d"]
        ],
    )


@router.get("/{entry_id}", response_model=WatchlistEntryOut, summary="Get one watchlist entry")
async def get_entry(
    entry_id: uuid.UUID, user: CurrentUser, session: SessionDep, container: ContainerDep
) -> WatchlistEntryOut:
    return entry_out(await WatchlistService(session, container).get(user, entry_id))


@router.patch(
    "/{entry_id}",
    response_model=WatchlistEntryOut,
    summary="Update alert preferences for an entry",
    description="Only fields present in the body are changed. Send an explicit null to "
    "clear target_price_minor, min_discount_pct or max_wait_days.",
)
async def update_entry(
    entry_id: uuid.UUID,
    body: WatchlistUpdate,
    user: CurrentUser,
    session: SessionDep,
    container: ContainerDep,
) -> WatchlistEntryOut:
    changes = body.model_dump(exclude_unset=True)
    if changes.get("channels") is not None:
        changes["channels"] = [c.value for c in body.channels or []]
    # Booleans cannot be cleared; ignore explicit nulls for them.
    for name in ("historical_low_only", "notify_on_buy", "is_active", "channels"):
        if name in changes and changes[name] is None:
            changes.pop(name)
    view = await WatchlistService(session, container).update(user, entry_id, changes)
    return entry_out(view)


@router.delete(
    "/{entry_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a watchlist entry"
)
async def delete_entry(
    entry_id: uuid.UUID, user: CurrentUser, session: SessionDep, container: ContainerDep
) -> Response:
    await WatchlistService(session, container).delete(user, entry_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
