"""Application settings.

All configuration comes from environment variables (optionally a local ``.env``).
Only configuration required by *enabled* features is validated at start-up.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEV_SECRET = "dev-only-insecure-secret-change-me-please-0123456789"  # noqa: S105


class ConfigurationError(RuntimeError):
    """Raised when configuration required by an enabled feature is missing."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- application -------------------------------------------------------
    app_name: str = "Steam4Caster"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True
    cors_allowed_origins: str = ""
    metrics_enabled: bool = True
    public_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:3000"

    # --- storage -----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://steam4caster:steam4caster@localhost:55432/steam4caster"
    redis_url: str = "redis://localhost:6379/0"
    # "redis" in real deployments; "memory" is a per-process stand-in for tests/dev.
    kv_backend: Literal["redis", "memory"] = "redis"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    # When true, enqueue requests are dropped (used by tests and one-off scripts).
    tasks_disabled: bool = False
    # Run the background jobs inside the API process (single-process deployments with
    # no Celery worker). Takes precedence over the Celery dispatcher.
    inline_jobs: bool = False

    # --- auth --------------------------------------------------------------
    jwt_secret_key: SecretStr = SecretStr(INSECURE_DEV_SECRET)
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 30
    # Fernet-compatible key material for push-subscription secrets. Any string works;
    # it is stretched with SHA-256. Defaults to the JWT secret outside production.
    secrets_encryption_key: SecretStr | None = None
    require_verified_email_for_alerts: bool = False

    # --- price provider ----------------------------------------------------
    price_provider: Literal["itad", "fake"] = "itad"
    itad_api_key: SecretStr | None = None
    itad_base_url: str = "https://api.isthereanydeal.com"
    itad_timeout_seconds: float = 15.0
    itad_max_retries: int = 3
    itad_max_retry_after_seconds: float = 30.0
    provider_user_agent: str = "Steam4Caster/0.1 (+https://github.com/; backend forecasting client)"
    steam_shop_name: str = "Steam"
    default_country: str = "US"
    default_currency: str = "USD"
    cache_ttl_search_seconds: int = 6 * 3600
    cache_ttl_metadata_seconds: int = 7 * 24 * 3600
    cache_ttl_shops_seconds: int = 24 * 3600
    cache_ttl_prices_seconds: int = 30 * 60
    cache_ttl_history_seconds: int = 3 * 3600
    # Reference exchange rates, shown only as an indicative cross-region comparison.
    fx_base_url: str = "https://api.frankfurter.dev/v1"
    cache_ttl_fx_seconds: int = 12 * 3600
    price_stale_after_seconds: int = 3 * 3600
    history_backfill_start: date = date(2012, 1, 1)
    history_overlap_hours: int = 48
    metadata_refresh_days: int = 14

    # --- forecasting -------------------------------------------------------
    model_artifact_path: str = "./artifacts"
    active_model_version: str | None = None
    forecast_max_age_hours: int = 24
    ml_enabled: bool = True
    training_schedule_enabled: bool = False
    training_min_new_observations: int = 5000
    training_min_rows: int = 300

    # --- recommendation policy (ruleset thresholds) ------------------------
    rec_default_max_wait_days: int = 30
    rec_waiting_cost_fraction: float = 0.05
    rec_near_low_tolerance: float = 0.05
    rec_wait_min_probability: float = 0.60
    rec_wait_min_savings_fraction: float = 0.10
    rec_buy_max_savings_fraction: float = 0.03
    rec_min_confidence: float = 0.35

    # --- notifications -----------------------------------------------------
    vapid_public_key: str | None = None
    vapid_private_key: SecretStr | None = None
    vapid_subject: str | None = None
    email_provider: Literal["smtp", "resend", "fake", "none"] = "smtp"
    resend_api_key: SecretStr | None = None
    resend_base_url: str = "https://api.resend.com"
    email_from: str = "Steam4Caster <alerts@localhost>"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False
    sms_enabled: bool = False
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_from_number: str | None = None
    alert_cooldown_hours: int = 24
    alert_max_per_user_per_day: int = 20
    alert_window_lead_days: int = 3
    alert_window_min_confidence: float = 0.7
    digest_hour_local: int = 9
    outbox_max_attempts: int = 6
    outbox_backoff_base_seconds: int = 30
    outbox_backoff_max_seconds: int = 3600
    outbox_sending_timeout_seconds: int = 600

    # --- rate limits (requests per window) ---------------------------------
    rate_limit_auth_per_minute: int = 10
    rate_limit_search_per_minute: int = 30
    rate_limit_test_notification_per_hour: int = 3

    # --- schedules ---------------------------------------------------------
    schedule_price_refresh_minutes: int = 180
    schedule_stagger_seconds: int = 300

    access_token_audience: str = Field(default="steam4caster-api")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def encryption_secret(self) -> str:
        if self.secrets_encryption_key is not None:
            return self.secrets_encryption_key.get_secret_value()
        return self.jwt_secret_key.get_secret_value()

    @property
    def web_push_configured(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key and self.vapid_subject)

    @property
    def sms_configured(self) -> bool:
        return bool(
            self.sms_enabled
            and self.twilio_account_sid
            and self.twilio_auth_token
            and self.twilio_from_number
        )

    def startup_problems(self) -> list[str]:
        """Return human-readable problems for configuration required by enabled features.

        Never includes secret values.
        """
        problems: list[str] = []
        if "*" in self.cors_origins:
            problems.append("CORS_ALLOWED_ORIGINS must be an explicit allowlist, not '*'.")
        if self.price_provider == "itad" and not self.itad_api_key:
            problems.append(
                "ITAD_API_KEY is required when PRICE_PROVIDER=itad "
                "(set PRICE_PROVIDER=fake for an explicit local demo dataset)."
            )
        if self.is_production:
            if self.jwt_secret_key.get_secret_value() == INSECURE_DEV_SECRET:
                problems.append("JWT_SECRET_KEY must be set to a strong secret in production.")
            if len(self.jwt_secret_key.get_secret_value()) < 32:
                problems.append("JWT_SECRET_KEY must be at least 32 characters.")
            if self.secrets_encryption_key is None:
                problems.append("SECRETS_ENCRYPTION_KEY is required in production.")
            if self.price_provider == "fake":
                problems.append("PRICE_PROVIDER=fake is not allowed in production.")
            if self.kv_backend != "redis":
                problems.append("KV_BACKEND must be 'redis' in production.")
            if self.email_provider == "fake":
                problems.append("EMAIL_PROVIDER=fake is not allowed in production.")
        if self.email_provider == "resend" and not self.resend_api_key:
            problems.append("RESEND_API_KEY is required when EMAIL_PROVIDER=resend.")
        if self.sms_enabled and not self.sms_configured:
            problems.append(
                "SMS_ENABLED=true requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN "
                "and TWILIO_FROM_NUMBER."
            )
        partial_vapid = [self.vapid_public_key, self.vapid_private_key, self.vapid_subject]
        if any(partial_vapid) and not all(partial_vapid):
            problems.append(
                "VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY and VAPID_SUBJECT must be set together."
            )
        return problems

    def validate_for_startup(self) -> None:
        problems = self.startup_problems()
        if problems:
            raise ConfigurationError("Invalid configuration: " + " | ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
