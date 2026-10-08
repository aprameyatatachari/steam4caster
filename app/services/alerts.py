"""Alert rule evaluation and transactional outbox creation.

An alert fires when a *condition becomes true for a new identity* (a new price, sale or
forecast), never merely because it is still true. The idempotency key is derived from
user, watchlist entry, trigger type, triggering identity and channel, and is enforced
by a unique constraint, so replays and concurrent workers cannot create duplicates.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.core.logging import get_logger
from app.core.metrics import ALERTS_CREATED, ALERTS_SUPPRESSED
from app.core.money import format_minor
from app.core.security import hash_token
from app.core.timeutil import in_quiet_hours, quiet_hours_end, utcnow
from app.db.session import insert_ignore
from app.models import (
    Forecast,
    Game,
    NotificationEvent,
    NotificationOutbox,
    NotificationPreference,
    RegionalGamePrice,
    Shop,
    User,
    WatchlistEntry,
)
from app.models.enums import Channel, DeliveryMode, OutboxState, RecommendationAction, TriggerType
from app.repositories import forecasts as forecasts_repo
from app.repositories import prices as prices_repo
from app.services.forecasts import ForecastService
from app.services.notifications import usable_channels

logger = get_logger(__name__)


@dataclass(frozen=True)
class Trigger:
    type: TriggerType
    identity: str
    title: str
    body: str


def idempotency_key(
    user_id: uuid.UUID, entry_id: uuid.UUID | None, trigger: str, identity: str, channel: str
) -> str:
    return hash_token(f"{user_id}|{entry_id}|{trigger}|{identity}|{channel}")


def _money(amount_minor: int, currency: str) -> str:
    return f"{currency} {format_minor(amount_minor, currency)}"


def price_triggers(
    entry: WatchlistEntry,
    game: Game,
    price: RegionalGamePrice,
    prior_low_minor: int | None,
) -> list[Trigger]:
    """Triggers that depend only on the current regional price."""
    identity = f"{price.price_minor}@{price.observed_at.isoformat()}"
    now_text = f"{_money(price.price_minor, price.currency)} on Steam ({price.country})"
    cut = f"{price.discount_pct}% off, " if price.discount_pct else ""
    triggers: list[Trigger] = []
    is_new_low = (
        price.discount_pct > 0
        and prior_low_minor is not None
        and price.price_minor < prior_low_minor
    )
    if is_new_low:
        assert prior_low_minor is not None
        triggers.append(
            Trigger(
                TriggerType.HISTORICAL_LOW, f"low:{identity}",
                f"{game.title} hit a new lowest recorded price",
                f"Now {cut}{now_text}. The previous lowest recorded price in this region was "
                f"{_money(prior_low_minor, price.currency)}.",
            )
        )  # fmt: skip
    if entry.historical_low_only:
        return triggers
    # A target is only comparable when it is in the same currency as the regional price.
    if (
        entry.target_price_minor is not None
        and entry.currency == price.currency
        and price.price_minor <= entry.target_price_minor
    ):
        triggers.append(
            Trigger(
                TriggerType.TARGET_PRICE, f"target:{identity}",
                f"{game.title} is at or below your target price",
                f"Now {cut}{now_text}. Your target was "
                f"{_money(entry.target_price_minor, price.currency)}.",
            )
        )  # fmt: skip
    if entry.min_discount_pct is not None and price.discount_pct >= entry.min_discount_pct:
        triggers.append(
            Trigger(
                TriggerType.MIN_DISCOUNT, f"discount:{identity}",
                f"{game.title} is {price.discount_pct}% off",
                f"Now {now_text}, meeting your minimum discount of {entry.min_discount_pct}%.",
            )
        )  # fmt: skip
    return triggers


class AlertService:
    def __init__(self, session: AsyncSession, container: Container) -> None:
        self.session = session
        self.container = container
        self.settings = container.settings

    async def evaluate_series(
        self, game: Game, shop: Shop, country: str, *, now: datetime | None = None
    ) -> int:
        """Evaluate every active watchlist entry for one game/region. Returns the number
        of outbox rows created. Safe to run repeatedly and concurrently."""
        now = now or utcnow()
        entries = list(
            await self.session.scalars(
                select(WatchlistEntry).where(
                    WatchlistEntry.game_id == game.id,
                    WatchlistEntry.country == country,
                    WatchlistEntry.is_active.is_(True),
                )
            )
        )
        if not entries:
            return 0
        price = await prices_repo.get_regional_price(self.session, game.id, shop.id, country)
        forecast = await forecasts_repo.latest_forecast(self.session, game.id, shop.id, country)
        prior_low = (
            await prices_repo.low_before(
                self.session, game.id, shop.id, country, price.currency, price.observed_at
            )
            if price is not None
            else None
        )
        users = {
            u.id: u
            for u in await self.session.scalars(
                select(User).where(User.id.in_({e.user_id for e in entries}))
            )
        }
        created = 0
        for entry in entries:
            user = users.get(entry.user_id)
            if user is None or not user.is_active:
                continue
            triggers = price_triggers(entry, game, price, prior_low) if price else []
            if forecast is not None:
                triggers.extend(await self._forecast_triggers(entry, game, forecast, now))
            url = price.url if price else game.provider_url
            # The transition alert goes first: its outbox row commits together with the
            # entry's updated recommendation state, so a crash cannot lose the transition.
            triggers.sort(key=lambda t: t.type != TriggerType.BUY_TRANSITION)
            for trigger in triggers:
                created += await self.create_alert(
                    user, entry, game, trigger, url=url, now=now, channels=entry.channels
                )
        # Persist recommendation state for entries that raised no alert this round.
        await self.session.commit()
        return created

    async def _forecast_triggers(
        self, entry: WatchlistEntry, game: Game, forecast: Forecast, now: datetime
    ) -> list[Trigger]:
        triggers: list[Trigger] = []
        recommendation = await ForecastService(self.session, self.container).recommend(
            forecast, entry=entry
        )
        previous = entry.last_recommendation_action
        if (
            entry.notify_on_buy
            and not entry.historical_low_only
            and recommendation.action == RecommendationAction.BUY
            # The first evaluation is not a transition; the user can already see it.
            and previous in (RecommendationAction.WAIT, RecommendationAction.NEUTRAL)
        ):
            triggers.append(
                Trigger(
                    TriggerType.BUY_TRANSITION, f"forecast:{forecast.id}",
                    f"{game.title}: recommendation changed to BUY",
                    recommendation.summary,
                )
            )  # fmt: skip
        if previous != recommendation.action:
            # Persisted in the same transaction as the outbox rows created below.
            entry.last_recommendation_action = recommendation.action
        lead = self.settings.alert_window_lead_days
        if (
            not entry.historical_low_only
            and forecast.window_start is not None
            and not forecast.currently_on_sale
            and forecast.confidence_score >= self.settings.alert_window_min_confidence
            and 0 <= (forecast.window_start - now.date()).days <= lead
        ):
            pct = round(forecast.new_sale_prob_30d * 100)
            triggers.append(
                Trigger(
                    TriggerType.SALE_WINDOW_APPROACHING,
                    f"window:{forecast.window_start.isoformat()}",
                    f"{game.title}: a sale may be approaching",
                    f"A likely sale window is estimated to begin around "
                    f"{forecast.window_start.isoformat()} (about {pct}% estimated chance of a "
                    "sale within 30 days). This is an estimate, not a guarantee.",
                )
            )  # fmt: skip
        return triggers

    async def _in_cooldown(
        self, entry_id: uuid.UUID, trigger: str, channel: str, since: datetime
    ) -> bool:
        stmt = (
            select(func.count())
            .select_from(NotificationOutbox)
            .join(NotificationEvent, NotificationEvent.id == NotificationOutbox.event_id)
            .where(
                NotificationEvent.watchlist_entry_id == entry_id,
                NotificationEvent.event_type == trigger,
                NotificationOutbox.channel == channel,
                NotificationOutbox.state != OutboxState.SUPPRESSED.value,
                NotificationOutbox.created_at >= since,
            )
        )
        return bool(await self.session.scalar(stmt))

    async def _recent_count(self, user_id: uuid.UUID, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(NotificationOutbox)
            .where(
                NotificationOutbox.user_id == user_id,
                NotificationOutbox.created_at >= since,
                NotificationOutbox.state != OutboxState.SUPPRESSED.value,
            )
        )
        return int(await self.session.scalar(stmt) or 0)

    async def create_alert(
        self,
        user: User,
        entry: WatchlistEntry,
        game: Game,
        trigger: Trigger,
        *,
        url: str | None,
        now: datetime,
        channels: list[str],
    ) -> int:
        """Create the event and its outbox rows in one transaction.

        Rows that must not be delivered (cooldown, per-user limit) are still written as
        SUPPRESSED so the same identity is never reconsidered.
        """
        usable = await usable_channels(self.session, self.container, user)
        wanted: dict[Channel, NotificationPreference] = {
            Channel(c): usable[Channel(c)] for c in channels if Channel(c) in usable
        }
        if not wanted:
            await self.session.commit()
            return 0
        keys = {
            channel: idempotency_key(
                user.id, entry.id, trigger.type.value, trigger.identity, channel.value
            )
            for channel in wanted
        }
        existing = set(
            await self.session.scalars(
                select(NotificationOutbox.idempotency_key).where(
                    NotificationOutbox.idempotency_key.in_(keys.values())
                )
            )
        )
        fresh = {c: p for c, p in wanted.items() if keys[c] not in existing}
        if not fresh:
            await self.session.commit()
            return 0

        event_id = uuid.uuid4()
        self.session.add(
            NotificationEvent(
                id=event_id,
                user_id=user.id,
                game_id=game.id,
                watchlist_entry_id=entry.id,
                event_type=trigger.type.value,
                trigger_identity=trigger.identity,
                title=trigger.title,
                body=trigger.body,
                url=url,
                payload={"game_title": game.title, "country": entry.country},
                created_at=now,
            )
        )
        await self.session.flush()
        cooldown_since = now - timedelta(hours=self.settings.alert_cooldown_hours)
        recent = await self._recent_count(user.id, now - timedelta(hours=24))
        rows: list[dict[str, Any]] = []
        for channel, pref in fresh.items():
            state, error, next_attempt = OutboxState.PENDING, None, now
            cooling = self.settings.alert_cooldown_hours > 0 and await self._in_cooldown(
                entry.id, trigger.type.value, channel.value, cooldown_since
            )
            if cooling:
                state, error = OutboxState.SUPPRESSED, "COOLDOWN"
            elif recent >= self.settings.alert_max_per_user_per_day:
                state, error = OutboxState.SUPPRESSED, "USER_DAILY_LIMIT"
            elif in_quiet_hours(now, pref.quiet_hours_start, pref.quiet_hours_end, pref.timezone):
                # Deferred, not dropped: delivered when the quiet window ends.
                next_attempt = quiet_hours_end(
                    now, pref.quiet_hours_start, pref.quiet_hours_end, pref.timezone
                )
                ALERTS_SUPPRESSED.labels(trigger.type.value, "QUIET_HOURS_DEFERRED").inc()
            if state == OutboxState.PENDING:
                recent += 1
            else:
                ALERTS_SUPPRESSED.labels(trigger.type.value, error or "UNKNOWN").inc()
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "event_id": event_id,
                    "user_id": user.id,
                    "channel": channel.value,
                    "idempotency_key": keys[channel],
                    "state": state.value,
                    "is_digest": pref.delivery_mode == DeliveryMode.DIGEST.value,
                    "attempt_count": 0,
                    "next_attempt_at": next_attempt,
                    "error_code": error,
                    "created_at": now,
                }
            )
        inserted = await insert_ignore(
            self.session, NotificationOutbox.__table__, rows, ["idempotency_key"]
        )
        if inserted == 0:
            # Another worker won the race for every channel; drop the orphan event.
            await self.session.execute(
                delete(NotificationEvent).where(NotificationEvent.id == event_id)
            )
            await self.session.commit()
            return 0
        await self.session.commit()
        for row in rows:
            if row["state"] == OutboxState.PENDING.value:
                ALERTS_CREATED.labels(trigger.type.value, row["channel"]).inc()
        return inserted
