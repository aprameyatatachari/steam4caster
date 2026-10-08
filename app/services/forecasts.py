"""Forecast generation, retrieval and recommendations.

Forecast rows are append-only: a new prediction is always a new row, and only the
outcome columns of an existing row are ever written again (by outcome evaluation).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.core.logging import get_logger
from app.core.money import discount_pct
from app.core.timeutil import utcnow
from app.forecasting.features.builder import build_state
from app.forecasting.inference.engine import forecast_series
from app.forecasting.policy import RULESET_VERSION, PolicyInput, decide
from app.forecasting.priors import GLOBAL_DEFAULT_PRIOR, choose_prior, prior_from_events
from app.forecasting.types import FEATURE_SCHEMA_VERSION, CohortPrior, GameContext
from app.models import Forecast, Game, Recommendation, Shop, WatchlistEntry
from app.repositories import forecasts as forecasts_repo
from app.repositories import prices as prices_repo
from app.services.prices import PriceService

logger = get_logger(__name__)

GLOBAL_PRIOR_CACHE_SECONDS = 6 * 3600


class ForecastService:
    def __init__(self, session: AsyncSession, container: Container) -> None:
        self.session = session
        self.container = container
        self.settings = container.settings

    async def _global_prior(self, shop: Shop, country: str) -> CohortPrior | None:
        key = f"prior:global:{shop.id}:{country}"
        cached = await self.container.kv.get(key)
        if cached is not None:
            data = json.loads(cached)
            return CohortPrior(**data) if data else None
        events = await prices_repo.cohort_events(self.session, shop.id, country, limit=20000)
        prior = prior_from_events("GLOBAL", events)
        await self.container.kv.set(
            key, json.dumps(prior.__dict__ if prior else None), GLOBAL_PRIOR_CACHE_SECONDS
        )
        return prior

    async def _prior(self, game: Game, shop: Shop, country: str) -> CohortPrior:
        """Hierarchical fallback: publisher cohort, then tag cohort, then global."""
        publisher = tag = None
        if game.primary_publisher:
            publisher = await prices_repo.cohort_events(
                self.session, shop.id, country, publisher=game.primary_publisher
            )
            publisher.pop(game.id, None)  # the game's own history is used directly
        if game.primary_tag:
            tag = await prices_repo.cohort_events(
                self.session, shop.id, country, primary_tag=game.primary_tag
            )
            tag.pop(game.id, None)
        prior = choose_prior(publisher, tag, None)
        if prior is GLOBAL_DEFAULT_PRIOR:
            prior = await self._global_prior(shop, country) or GLOBAL_DEFAULT_PRIOR
        return prior

    async def generate(
        self, game: Game, shop: Shop, country: str, *, now: datetime | None = None
    ) -> Forecast:
        """Create and persist a new immutable forecast from data visible at ``now``."""
        cutoff = now or utcnow()
        points, currency = await prices_repo.series_points(
            self.session, game.id, shop.id, country, until=cutoff
        )
        regional = await prices_repo.get_regional_price(self.session, game.id, shop.id, country)
        watermark = await prices_repo.get_watermark(self.session, game.id, shop.id, country)
        freshest = max(
            (
                t
                for t in (
                    watermark.last_success_at if watermark else None,
                    regional.fetched_at if regional else None,
                )
                if t is not None
            ),
            default=None,
        )
        data_age_days = max(0.0, (cutoff - freshest).total_seconds() / 86400) if freshest else 365.0
        # Only a low quoted in the same currency as the series is comparable.
        provider_low = (
            regional.historical_low_minor
            if regional is not None and currency is not None and regional.currency == currency
            else None
        )
        context = GameContext(
            release_date=game.release_date,
            publisher=game.primary_publisher,
            primary_tag=game.primary_tag,
            type=game.type,
        )
        state = build_state(
            points,
            context,
            cutoff,
            await self._prior(game, shop, country),
            historical_low_minor=provider_low,
            data_age_days=data_age_days,
        )
        model = await self.container.model_store.get_active(self.session)
        result = forecast_series(state, model)
        forecast = Forecast(
            game_id=game.id,
            shop_id=shop.id,
            country=country,
            currency=currency,
            created_at=utcnow() if now is None else now,
            cutoff_at=cutoff,
            method=result.method,
            currently_on_sale=result.currently_on_sale,
            sale_prob_7d=result.sale_probs[7],
            sale_prob_30d=result.sale_probs[30],
            sale_prob_90d=result.sale_probs[90],
            new_sale_prob_7d=result.new_sale_probs[7],
            new_sale_prob_30d=result.new_sale_probs[30],
            new_sale_prob_90d=result.new_sale_probs[90],
            discount_class_probs=result.tier_probs,
            expected_discount_pct=result.expected_discount_pct,
            current_price_minor=result.current_price_minor,
            regular_price_minor=result.regular_price_minor,
            historical_low_minor=result.historical_low_minor,
            price_lower_minor=result.price_lower_minor,
            price_median_minor=result.price_median_minor,
            price_upper_minor=result.price_upper_minor,
            window_start=result.window[0] if result.window else None,
            window_end=result.window[1] if result.window else None,
            confidence_score=result.confidence,
            data_quality=result.data_quality,
            explanation_factors=[f.as_dict() for f in result.factors],
            model_version=result.model_version,
            model_version_id=result.model_version_id,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            feature_snapshot=result.feature_snapshot,
        )
        self.session.add(forecast)
        await self.session.commit()
        return forecast

    async def get_or_generate(self, game: Game, shop: Shop, country: str) -> Forecast:
        """Latest forecast if still current, otherwise refresh data and forecast again."""
        prices = PriceService(self.session, self.container)
        await prices.ensure_history(game, shop, country)
        await prices.get_current(game, shop, country)
        latest = await forecasts_repo.latest_forecast(self.session, game.id, shop.id, country)
        if latest is not None:
            fresh = utcnow() - latest.created_at < timedelta(
                hours=self.settings.forecast_max_age_hours
            )
            # A backfill that lands after a forecast was made adds *older* observations,
            # so compare ingestion time as well as the price-change time.
            changed = await prices_repo.has_observations_newer_than(
                self.session,
                game.id,
                shop.id,
                country,
                observed_after=latest.cutoff_at,
                ingested_after=latest.created_at,
            )
            if fresh and not changed:
                return latest
        return await self.generate(game, shop, country)

    async def history(
        self,
        game: Game,
        shop: Shop,
        country: str,
        *,
        before: tuple[datetime, str] | None,
        limit: int,
    ) -> list[Forecast]:
        return await forecasts_repo.forecast_history(
            self.session, game.id, shop.id, country, before=before, limit=limit
        )

    async def recommend(
        self,
        forecast: Forecast,
        *,
        max_wait_days: int | None = None,
        entry: WatchlistEntry | None = None,
    ) -> Recommendation:
        """Apply the versioned policy to a forecast and persist the decision (idempotent
        per forecast, wait horizon and watchlist entry)."""
        config = self.container.policy_config
        wait = max_wait_days or (entry.max_wait_days if entry else None)
        wait = wait or config.default_max_wait_days
        entry_id: uuid.UUID | None = entry.id if entry else None
        existing = await forecasts_repo.find_recommendation(
            self.session, forecast.id, wait, entry_id
        )
        if existing is not None and existing.ruleset_version == RULESET_VERSION:
            return existing
        price, regular = forecast.current_price_minor, forecast.regular_price_minor
        decision = decide(
            PolicyInput(
                current_price_minor=price,
                regular_price_minor=regular,
                historical_low_minor=forecast.historical_low_minor,
                current_discount_pct=(
                    discount_pct(price, regular) if price is not None and regular else 0
                ),
                currently_on_sale=forecast.currently_on_sale,
                new_sale_probs={
                    7: forecast.new_sale_prob_7d,
                    30: forecast.new_sale_prob_30d,
                    90: forecast.new_sale_prob_90d,
                },
                tier_probs=forecast.discount_class_probs,
                confidence=forecast.confidence_score,
                currency=forecast.currency,
                max_wait_days=wait,
            ),
            config,
        )
        recommendation = Recommendation(
            forecast_id=forecast.id,
            watchlist_entry_id=entry_id,
            action=decision.action,
            score=decision.score,
            currency=forecast.currency,
            expected_savings_minor=decision.expected_savings_minor,
            expected_future_price_minor=decision.expected_future_price_minor,
            waiting_cost_minor=decision.waiting_cost_minor,
            max_wait_days=decision.max_wait_days,
            selected_horizon_days=decision.selected_horizon_days,
            sale_probability=decision.sale_probability,
            reason_codes=decision.reason_codes,
            summary=decision.summary,
            thresholds=decision.thresholds,
            ruleset_version=RULESET_VERSION,
        )
        self.session.add(recommendation)
        await self.session.commit()
        return recommendation
