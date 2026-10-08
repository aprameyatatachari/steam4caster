from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import ContainerDep, CurrentUser, SessionDep, rate_limit_by_user
from app.core.errors import NotFoundError, ValidationFailed
from app.core.pagination import decode_cursor, encode_cursor
from app.core.timeutil import ensure_utc
from app.providers.pricing.base import ATTRIBUTION
from app.schemas.catalog import (
    ForecastOut,
    ForecastPage,
    GameOut,
    HistoryPage,
    PriceOut,
    RecommendationOut,
    SaleEventOut,
    forecast_out,
    game_out,
    history_point,
    price_out,
    recommendation_out,
    sale_event_out,
)
from app.schemas.common import Attribution, CountryCode
from app.services.catalog import CatalogService, provider_errors
from app.services.forecasts import ForecastService
from app.services.prices import PriceService

router = APIRouter(prefix="/games", tags=["games"])

CountryQuery = Annotated[
    CountryCode | None,
    Query(description="ISO 3166-1 alpha-2 country. Defaults to the user's country."),
]
ShopQuery = Annotated[str, Query(description="Shop slug. Only `steam` is supported.")]


def _aware(value: datetime | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        return ensure_utc(value)
    except ValueError as exc:
        raise ValidationFailed(f"`{name}` must include a UTC offset.") from exc


@router.get(
    "/search",
    response_model=list[GameOut],
    dependencies=[Depends(rate_limit_by_user("search", "rate_limit_search_per_minute", 60))],
    summary="Search games by title",
    description="Searches the provider (cached), stores any new games and returns internal "
    "ids together with external identifiers.",
)
async def search(
    user: CurrentUser,
    session: SessionDep,
    container: ContainerDep,
    q: Annotated[str, Query(min_length=2, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[GameOut]:
    games = await CatalogService(session, container).search(q, limit)
    return [game_out(g) for g in games]


@router.get(
    "/lookup",
    response_model=GameOut,
    dependencies=[Depends(rate_limit_by_user("search", "rate_limit_search_per_minute", 60))],
    summary="Resolve a game from a Steam app id or exact title",
)
async def lookup(
    user: CurrentUser,
    session: SessionDep,
    container: ContainerDep,
    steam_app_id: Annotated[int | None, Query(ge=1)] = None,
    title: Annotated[str | None, Query(min_length=2, max_length=200)] = None,
) -> GameOut:
    if (steam_app_id is None) == (title is None):
        raise ValidationFailed("Provide exactly one of `steam_app_id` or `title`.")
    game = await CatalogService(session, container).lookup(steam_app_id=steam_app_id, title=title)
    return game_out(game)


@router.get("/{game_id}", response_model=GameOut, summary="Game metadata")
async def get_game(
    game_id: uuid.UUID, user: CurrentUser, session: SessionDep, container: ContainerDep
) -> GameOut:
    return game_out(await CatalogService(session, container).get_with_info(game_id))


@router.get(
    "/{game_id}/prices",
    response_model=PriceOut,
    summary="Current regional Steam price, discount and historical low",
)
async def get_prices(
    game_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    container: ContainerDep,
    country: CountryQuery = None,
    shop: ShopQuery = "steam",
) -> PriceOut:
    catalog = CatalogService(session, container)
    game = await catalog.get(game_id)
    shop_row = await catalog.resolve_shop(shop)
    region = country or user.default_country
    async with provider_errors():
        row = await PriceService(session, container).get_current(game, shop_row, region)
    if row is None:
        raise NotFoundError(f"No Steam price is available for this game in {region}.")
    return price_out(row, shop_row.slug, container.settings.price_stale_after_seconds)


@router.get(
    "/{game_id}/history",
    response_model=HistoryPage,
    summary="Timestamped regional price history",
    description="Cursor-paginated price changes in chronological order. Prices are the "
    "region's own; nothing is converted between currencies.",
)
async def get_history(
    game_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    container: ContainerDep,
    country: CountryQuery = None,
    shop: ShopQuery = "steam",
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> HistoryPage:
    catalog = CatalogService(session, container)
    game = await catalog.get(game_id)
    shop_row = await catalog.resolve_shop(shop)
    region = country or user.default_country
    prices = PriceService(session, container)
    await prices.ensure_history(game, shop_row, region)
    after = None
    if cursor:
        at, last_id = decode_cursor(cursor)
        after = (at, int(last_id))
    rows = await prices.list_observations(
        game, shop_row, region,
        start=_aware(from_, "from"), end=_aware(to, "to"), after=after, limit=limit + 1,
    )  # fmt: skip
    page, more = rows[:limit], len(rows) > limit
    return HistoryPage(
        game_id=game.id,
        shop=shop_row.slug,
        country=region,
        items=[history_point(r) for r in page],
        next_cursor=encode_cursor(page[-1].observed_at, page[-1].id) if more else None,
        attribution=Attribution(**ATTRIBUTION),
    )


@router.get(
    "/{game_id}/sale-events",
    response_model=list[SaleEventOut],
    summary="Sale periods derived from the price history",
)
async def get_sale_events(
    game_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    container: ContainerDep,
    country: CountryQuery = None,
    shop: ShopQuery = "steam",
) -> list[SaleEventOut]:
    catalog = CatalogService(session, container)
    game = await catalog.get(game_id)
    shop_row = await catalog.resolve_shop(shop)
    region = country or user.default_country
    prices = PriceService(session, container)
    await prices.ensure_history(game, shop_row, region)
    return [sale_event_out(e) for e in await prices.list_sale_events(game, shop_row, region)]


@router.get(
    "/{game_id}/forecast",
    response_model=ForecastOut,
    summary="Probabilistic sale forecast",
    description="Returns the current forecast, generating a new immutable one when the "
    "latest is stale or the price has changed since it was made.",
)
async def get_forecast(
    game_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    container: ContainerDep,
    country: CountryQuery = None,
    shop: ShopQuery = "steam",
) -> ForecastOut:
    catalog = CatalogService(session, container)
    game = await catalog.get_with_info(game_id)
    shop_row = await catalog.resolve_shop(shop)
    async with provider_errors():
        forecast = await ForecastService(session, container).get_or_generate(
            game, shop_row, country or user.default_country
        )
    return forecast_out(forecast, shop_row.slug)


@router.get(
    "/{game_id}/recommendation",
    response_model=RecommendationOut,
    summary="BUY / WAIT / NEUTRAL recommendation",
    description="Applies the versioned rule policy to the current forecast. An estimate, "
    "not a guarantee or financial advice.",
)
async def get_recommendation(
    game_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    container: ContainerDep,
    country: CountryQuery = None,
    shop: ShopQuery = "steam",
    max_wait_days: Annotated[int | None, Query(ge=1, le=365)] = None,
) -> RecommendationOut:
    catalog = CatalogService(session, container)
    game = await catalog.get_with_info(game_id)
    shop_row = await catalog.resolve_shop(shop)
    service = ForecastService(session, container)
    async with provider_errors():
        forecast = await service.get_or_generate(game, shop_row, country or user.default_country)
    return recommendation_out(await service.recommend(forecast, max_wait_days=max_wait_days))


@router.get(
    "/{game_id}/forecast-history",
    response_model=ForecastPage,
    summary="Past forecasts and their outcomes (newest first)",
)
async def get_forecast_history(
    game_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    container: ContainerDep,
    country: CountryQuery = None,
    shop: ShopQuery = "steam",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ForecastPage:
    catalog = CatalogService(session, container)
    game = await catalog.get(game_id)
    shop_row = await catalog.resolve_shop(shop)
    rows = await ForecastService(session, container).history(
        game, shop_row, country or user.default_country,
        before=decode_cursor(cursor) if cursor else None, limit=limit + 1,
    )  # fmt: skip
    page, more = rows[:limit], len(rows) > limit
    return ForecastPage(
        items=[forecast_out(f, shop_row.slug) for f in page],
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if more else None,
    )
