from __future__ import annotations

import json
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field

from app.api.deps import ContainerDep, CurrentUser
from app.core.errors import UpstreamUnavailable
from app.providers.fx import SOURCE_NAME, SOURCE_URL, FxRate, FxUnavailable
from app.schemas.common import ApiModel, CurrencyCode

router = APIRouter(tags=["games"])


class FxRateOut(ApiModel):
    base: str
    quote: str
    rate: float = Field(description="One unit of `base` in `quote`.")
    as_of: date = Field(description="Date of the reference rate.")
    source: str
    source_url: str
    note: str = (
        "Indicative only. Steam sets each region's price separately, so a converted "
        "price is not what the game costs in that region."
    )


@router.get(
    "/fx",
    response_model=FxRateOut,
    summary="Reference exchange rate for comparing prices across regions",
    description="A daily reference rate, cached. It is shown beside another region's "
    "prices for rough comparison and is never used for stored prices or forecasts.",
)
async def get_fx_rate(
    user: CurrentUser,
    container: ContainerDep,
    base: Annotated[CurrencyCode, Query(description="Currency to convert from.")],
    quote: Annotated[CurrencyCode, Query(description="Currency to convert to.")],
) -> FxRateOut:
    if base == quote:
        return FxRateOut(
            base=base,
            quote=quote,
            rate=1.0,
            as_of=date.today(),
            source=SOURCE_NAME,
            source_url=SOURCE_URL,
        )
    key = f"fx:{base}:{quote}"
    cached = await container.kv.get(key)
    if cached is not None:
        data = json.loads(cached)
        rate = FxRate(base, quote, float(data["rate"]), date.fromisoformat(data["as_of"]))
    else:
        try:
            rate = await container.fx.get_rate(base, quote)
        except FxUnavailable as exc:
            raise UpstreamUnavailable(
                f"No exchange rate is available for {base} to {quote} right now."
            ) from exc
        await container.kv.set(
            key,
            json.dumps({"rate": rate.rate, "as_of": rate.as_of.isoformat()}),
            container.settings.cache_ttl_fx_seconds,
        )
    return FxRateOut(
        base=base,
        quote=quote,
        rate=rate.rate,
        as_of=rate.as_of,
        source=SOURCE_NAME,
        source_url=SOURCE_URL,
    )
