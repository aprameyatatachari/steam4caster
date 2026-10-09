"""Process-wide dependency container: settings, storage, providers and task dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import ConfigurationError, Settings
from app.core.kv import KeyValueStore, MemoryKV, RedisKV, ResilientKV
from app.core.logging import get_logger
from app.core.security import FieldCipher
from app.db.session import Database
from app.forecasting.inference.registry import ModelStore
from app.forecasting.policy import PolicyConfig
from app.models.enums import Channel
from app.providers.notifications.base import FakeNotificationProvider, NotificationProvider
from app.providers.pricing.base import PriceDataProvider
from app.providers.pricing.caching import CacheTTLs, CachingPriceProvider
from app.providers.steam import SteamCommunityProvider, SteamProfileProvider

logger = get_logger(__name__)


class TaskDispatcher(Protocol):
    def enqueue(self, name: str, *args: Any, countdown: float | None = None) -> None: ...


class CeleryDispatcher:
    """Enqueues background work. A broker outage never fails the calling request: the
    periodic schedules pick the work up later."""

    def enqueue(self, name: str, *args: Any, countdown: float | None = None) -> None:
        from app.tasks.celery_app import celery_app

        try:
            celery_app.send_task(name, args=list(args), countdown=countdown)
        except Exception:
            logger.warning("could not enqueue task", extra={"task": name})


class RecordingDispatcher:
    """Collects enqueue requests instead of sending them (tests, scripts, TASKS_DISABLED)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def enqueue(self, name: str, *args: Any, countdown: float | None = None) -> None:
        self.calls.append((name, args))


@dataclass
class Container:
    settings: Settings
    db: Database
    kv: KeyValueStore
    price_provider: PriceDataProvider
    notifiers: dict[Channel, NotificationProvider]
    model_store: ModelStore
    cipher: FieldCipher
    tasks: TaskDispatcher
    steam: SteamProfileProvider
    _closed: bool = field(default=False, repr=False)

    @property
    def policy_config(self) -> PolicyConfig:
        s = self.settings
        return PolicyConfig(
            waiting_cost_fraction=s.rec_waiting_cost_fraction,
            near_low_tolerance=s.rec_near_low_tolerance,
            wait_min_probability=s.rec_wait_min_probability,
            wait_min_savings_fraction=s.rec_wait_min_savings_fraction,
            buy_max_savings_fraction=s.rec_buy_max_savings_fraction,
            min_confidence=s.rec_min_confidence,
            default_max_wait_days=s.rec_default_max_wait_days,
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.price_provider.aclose()
        await self.steam.aclose()
        for provider in self.notifiers.values():
            await provider.aclose()
        await self.kv.close()
        await self.db.dispose()


def build_price_provider(settings: Settings, kv: KeyValueStore) -> PriceDataProvider:
    inner: PriceDataProvider
    if settings.price_provider == "fake":
        if settings.is_production:
            raise ConfigurationError("PRICE_PROVIDER=fake is not allowed in production.")
        from app.providers.pricing.fake import FakePriceProvider

        logger.warning("using the synthetic fake price provider; data is not real")
        inner = FakePriceProvider()
    else:
        if not settings.itad_api_key:
            raise ConfigurationError("ITAD_API_KEY is required when PRICE_PROVIDER=itad.")
        from app.providers.pricing.itad import IsThereAnyDealProvider

        inner = IsThereAnyDealProvider(
            settings.itad_api_key.get_secret_value(),
            base_url=settings.itad_base_url,
            user_agent=settings.provider_user_agent,
            timeout=settings.itad_timeout_seconds,
            max_retries=settings.itad_max_retries,
            max_retry_after=settings.itad_max_retry_after_seconds,
        )
    ttls = CacheTTLs(
        search=settings.cache_ttl_search_seconds,
        metadata=settings.cache_ttl_metadata_seconds,
        shops=settings.cache_ttl_shops_seconds,
        prices=settings.cache_ttl_prices_seconds,
        history=settings.cache_ttl_history_seconds,
    )
    return CachingPriceProvider(inner, kv, ttls)


def build_notifiers(settings: Settings) -> dict[Channel, NotificationProvider]:
    from app.providers.notifications.email import ResendEmailProvider, SmtpEmailProvider
    from app.providers.notifications.sms import DisabledSmsProvider, TwilioSmsProvider
    from app.providers.notifications.webpush import WebPushProvider

    notifiers: dict[Channel, NotificationProvider] = {}
    if settings.web_push_configured:
        assert settings.vapid_private_key and settings.vapid_subject
        notifiers[Channel.WEB_PUSH] = WebPushProvider(
            settings.vapid_private_key.get_secret_value(), settings.vapid_subject
        )
    if settings.email_provider == "resend":
        assert settings.resend_api_key
        notifiers[Channel.EMAIL] = ResendEmailProvider(
            settings.resend_api_key.get_secret_value(),
            settings.email_from,
            base_url=settings.resend_base_url,
        )
    elif settings.email_provider == "smtp":
        notifiers[Channel.EMAIL] = SmtpEmailProvider(
            settings.smtp_host,
            settings.smtp_port,
            settings.email_from,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
            starttls=settings.smtp_starttls,
        )
    elif settings.email_provider == "fake":
        notifiers[Channel.EMAIL] = FakeNotificationProvider(Channel.EMAIL)
    if settings.sms_configured:
        assert settings.twilio_account_sid and settings.twilio_auth_token
        assert settings.twilio_from_number
        notifiers[Channel.SMS] = TwilioSmsProvider(
            settings.twilio_account_sid,
            settings.twilio_auth_token.get_secret_value(),
            settings.twilio_from_number,
        )
    else:
        notifiers[Channel.SMS] = DisabledSmsProvider()
    return notifiers


def build_container(settings: Settings, *, pooled: bool = True) -> Container:
    settings.validate_for_startup()
    kv: KeyValueStore = (
        ResilientKV(RedisKV(settings.redis_url)) if settings.kv_backend == "redis" else MemoryKV()
    )
    return Container(
        settings=settings,
        db=Database(settings.database_url, pooled=pooled),
        kv=kv,
        price_provider=build_price_provider(settings, kv),
        notifiers=build_notifiers(settings),
        model_store=ModelStore(
            enabled=settings.ml_enabled, pinned_version=settings.active_model_version
        ),
        cipher=FieldCipher(settings.encryption_secret),
        tasks=RecordingDispatcher() if settings.tasks_disabled else CeleryDispatcher(),
        steam=SteamCommunityProvider(user_agent=settings.provider_user_agent),
    )
