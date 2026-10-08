from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.core.config import INSECURE_DEV_SECRET, ConfigurationError, Settings
from app.core.kv import MemoryKV, distributed_lock, hit_rate_limit
from app.core.logging import JsonFormatter, mask_endpoint, redact
from app.core.money import (
    CurrencyMismatch,
    apply_discount,
    discount_pct,
    ensure_same_currency,
    format_minor,
    normalize_country,
    normalize_currency,
)
from app.core.pagination import decode_cursor, encode_cursor
from app.core.security import (
    FieldCipher,
    TokenError,
    create_access_token,
    decode_access_token,
    generate_vapid_keypair,
    hash_password,
    verify_password,
)
from app.core.timeutil import in_quiet_hours, quiet_hours_end
from app.forecasting.buckets import (
    BUCKET_NAMES,
    bucket_for,
    discount_quantile,
    expected_discount,
    normalize,
    price_interval,
)
from app.forecasting.calendar import next_window, seasonal_windows
from app.forecasting.sale_events import derive_sale_events
from app.forecasting.types import PricePoint
from app.services.alerts import idempotency_key
from app.services.dispatcher import backoff_seconds
from tests.conftest import make_settings
from tests.helpers import NOW, price_points

# --- money ------------------------------------------------------------------


def test_minor_units_format_without_floats() -> None:
    assert format_minor(149900, "INR") == "1499.00"
    assert format_minor(1999, "USD") == "19.99"
    assert format_minor(7900, "JPY") == "7900"  # zero-decimal currency
    assert format_minor(1500, "KWD") == "1.500"  # three-decimal currency
    assert format_minor(5, "USD") == "0.05"


def test_currency_and_country_normalisation() -> None:
    assert normalize_currency(" inr ") == "INR"
    assert normalize_country("in") == "IN"
    with pytest.raises(ValueError):
        normalize_currency("RUPEE")
    with pytest.raises(ValueError):
        normalize_country("IND")


def test_mixed_currencies_are_never_comparable() -> None:
    assert ensure_same_currency("INR", None, "INR") == "INR"
    with pytest.raises(CurrencyMismatch):
        ensure_same_currency("INR", "USD")


def test_discount_helpers_clamp() -> None:
    assert discount_pct(1000, 2000) == 50
    assert discount_pct(2000, 2000) == 0
    assert discount_pct(2500, 2000) == 0  # price above regular is not a discount
    assert apply_discount(2000, 0.5) == 1000
    assert apply_discount(2000, 1.7) == 0  # never negative
    assert apply_discount(2000, -0.3) == 2000  # never above regular


# --- sale events ------------------------------------------------------------


def test_sale_events_are_contiguous_discounted_periods() -> None:
    points = price_points(NOW, sales=[(200, 7, 50), (100, 14, 30)])
    events = derive_sale_events(points)
    assert [e.max_discount_pct for e in events] == [50, 30]
    assert all(e.ended_at is not None for e in events)
    assert events[0].ended_at - events[0].started_at == timedelta(days=7)
    assert events[0].min_price_minor == 1000 and events[0].regular_minor == 2000


def test_depth_change_inside_a_sale_stays_one_event() -> None:
    start = NOW - timedelta(days=20)
    points = [
        PricePoint(NOW - timedelta(days=100), 2000, 2000, 0),
        PricePoint(start, 1600, 2000, 20),
        PricePoint(start + timedelta(days=2), 1000, 2000, 50),  # deepened mid-sale
        PricePoint(start + timedelta(days=7), 2000, 2000, 0),
    ]
    (event,) = derive_sale_events(points)
    assert event.initial_price_minor == 1600
    assert event.min_price_minor == 1000
    assert event.max_discount_pct == 50


def test_running_sale_has_no_end_and_duplicates_are_ignored() -> None:
    points = price_points(NOW, sales=[(3, 0, 40)])
    events = derive_sale_events(points + points)  # replayed log
    assert len(events) == 1
    assert events[0].ended_at is None


def test_sale_derivation_is_order_independent() -> None:
    points = price_points(NOW, sales=[(300, 7, 50), (200, 7, 25), (100, 7, 75)])
    assert derive_sale_events(points) == derive_sale_events(list(reversed(points)))


# --- discount tiers ---------------------------------------------------------


@pytest.mark.parametrize(
    ("pct", "bucket"),
    [(5, "LT_20"), (19, "LT_20"), (20, "20_TO_29"), (29, "20_TO_29"), (30, "30_TO_39"),
     (49, "40_TO_49"), (50, "50_TO_59"), (60, "60_TO_74"), (74, "60_TO_74"), (75, "75_PLUS"),
     (95, "75_PLUS")],
)  # fmt: skip
def test_bucket_boundaries(pct: int, bucket: str) -> None:
    assert bucket_for(pct) == bucket


def test_price_interval_is_ordered_and_bounded() -> None:
    probs = normalize({"50_TO_59": 0.7, "60_TO_74": 0.2, "30_TO_39": 0.1})
    lower, median, upper = price_interval(149900, probs)
    assert 0 <= lower <= median <= upper <= 149900
    assert discount_quantile(probs, 0.5) == pytest.approx(50 + 9 * (0.4 / 0.7))
    assert 30 < expected_discount(probs) < 67


def test_price_interval_never_negative_for_extreme_distribution() -> None:
    lower, median, upper = price_interval(99, {"75_PLUS": 1.0})
    assert 0 <= lower <= median <= upper <= 99


def test_normalize_handles_missing_and_empty() -> None:
    assert sum(normalize({}).values()) == pytest.approx(1.0)
    assert set(normalize({"LT_20": 2.0})) == set(BUCKET_NAMES)


# --- seasonal calendar ------------------------------------------------------


def test_seasonal_windows_match_known_years() -> None:
    by_kind = {w.kind: w for w in seasonal_windows(2024)}
    assert by_kind["SUMMER"].start == date(2024, 6, 27)
    assert by_kind["WINTER"].start == date(2024, 12, 19)
    assert by_kind["SPRING"].start == date(2024, 3, 14)
    assert by_kind["AUTUMN"].start == date(2024, 11, 26)
    assert {w.kind for w in seasonal_windows(2022)} == {"SUMMER", "AUTUMN", "WINTER"}


def test_next_window_crosses_year_boundary() -> None:
    window = next_window(date(2025, 12, 30))
    assert window.start.year == 2026 and window.kind == "SPRING"


# --- quiet hours ------------------------------------------------------------


def test_quiet_hours_wrap_midnight_in_user_timezone() -> None:
    start, end = time(22, 0), time(7, 0)
    # 18:00 UTC is 23:30 in Kolkata -> quiet. 04:00 UTC is 09:30 -> not quiet.
    assert in_quiet_hours(datetime(2026, 5, 4, 18, 0, tzinfo=UTC), start, end, "Asia/Kolkata")
    assert not in_quiet_hours(datetime(2026, 5, 4, 4, 0, tzinfo=UTC), start, end, "Asia/Kolkata")
    resume = quiet_hours_end(datetime(2026, 5, 4, 18, 0, tzinfo=UTC), start, end, "Asia/Kolkata")
    assert resume == datetime(2026, 5, 5, 1, 30, tzinfo=UTC)  # 07:00 IST


def test_quiet_hours_disabled_when_unset_or_empty() -> None:
    now = datetime(2026, 5, 4, 3, 0, tzinfo=UTC)
    assert not in_quiet_hours(now, None, None, "UTC")
    assert not in_quiet_hours(now, time(3, 0), time(3, 0), "UTC")
    assert quiet_hours_end(now, None, None, "UTC") == now


# --- security ---------------------------------------------------------------


def test_password_hash_is_argon2id_and_verifies() -> None:
    hashed = hash_password("correct-horse")
    assert hashed.startswith("$argon2id$")
    assert verify_password("correct-horse", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("anything", None)


def test_access_token_roundtrip_and_tamper_detection(settings: Settings) -> None:
    user_id = uuid.uuid4()
    token = create_access_token(settings, user_id)
    assert decode_access_token(settings, token) == user_id
    with pytest.raises(TokenError):
        decode_access_token(settings, token[:-2] + "xx")
    other = settings.model_copy(update={"access_token_audience": "someone-else"})
    with pytest.raises(TokenError):
        decode_access_token(other, token)


def test_field_cipher_roundtrip_and_wrong_key() -> None:
    cipher = FieldCipher("key-one")
    secret = cipher.encrypt("p256dh-material")
    assert "p256dh-material" not in secret
    assert cipher.decrypt(secret) == "p256dh-material"
    with pytest.raises(ValueError):
        FieldCipher("key-two").decrypt(secret)


def test_vapid_keypair_shape() -> None:
    public, private = generate_vapid_keypair()
    assert len(public) == 87 and len(private) == 43  # 65 and 32 raw bytes, unpadded base64url


# --- logging redaction ------------------------------------------------------


def test_log_redaction_removes_secrets_and_pii() -> None:
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1,
        "login for player@example.com with Bearer abc.def.ghi key=/x?key=SECRETKEY phone +14155550123",
        None, None,
    )  # fmt: skip
    record.password = "hunter2"
    record.endpoint = "https://push.example/send/very-secret-capability"
    record.nested = {"auth_token": "t0ken", "safe": "value"}
    line = JsonFormatter().format(record)
    payload = json.loads(line)
    for secret in ("hunter2", "player@example.com", "abc.def.ghi", "SECRETKEY", "t0ken",
                   "very-secret-capability", "+14155550123"):  # fmt: skip
        assert secret not in line
    assert payload["nested"]["safe"] == "value"
    assert redact({"api_key": "x"}) == {"api_key": "[REDACTED]"}
    assert mask_endpoint("https://fcm.googleapis.com/fcm/send/abcdef123456") == (
        "fcm.googleapis.com/…123456"
    )


# --- configuration ----------------------------------------------------------


def test_missing_twilio_does_not_block_startup(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(tmp_path, sms_enabled=False)
    settings.validate_for_startup()
    assert not settings.sms_configured


def test_enabled_features_fail_fast_without_their_config(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigurationError, match="TWILIO"):
        make_settings(tmp_path, sms_enabled=True).validate_for_startup()
    with pytest.raises(ConfigurationError, match="ITAD_API_KEY"):
        make_settings(tmp_path, price_provider="itad").validate_for_startup()
    with pytest.raises(ConfigurationError, match="RESEND_API_KEY"):
        make_settings(tmp_path, email_provider="resend").validate_for_startup()


def test_production_rejects_insecure_defaults_and_wildcard_cors(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(
        tmp_path, app_env="production", jwt_secret_key=INSECURE_DEV_SECRET,
        cors_allowed_origins="*",
    )  # fmt: skip
    problems = " ".join(settings.startup_problems())
    assert "JWT_SECRET_KEY" in problems
    assert "explicit allowlist" in problems
    assert "PRICE_PROVIDER=fake" in problems
    assert INSECURE_DEV_SECRET not in problems  # problems never echo secret values


# --- misc -------------------------------------------------------------------


def test_cursor_roundtrip_and_rejects_garbage() -> None:
    at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert decode_cursor(encode_cursor(at, 42)) == (at, "42")
    from app.core.errors import ValidationFailed

    with pytest.raises(ValidationFailed):
        decode_cursor("not-a-cursor")


def test_idempotency_key_depends_on_every_component() -> None:
    user, entry = uuid.uuid4(), uuid.uuid4()
    base = idempotency_key(user, entry, "TARGET_PRICE", "1000@t", "EMAIL")
    assert base == idempotency_key(user, entry, "TARGET_PRICE", "1000@t", "EMAIL")
    variants = {
        idempotency_key(uuid.uuid4(), entry, "TARGET_PRICE", "1000@t", "EMAIL"),
        idempotency_key(user, uuid.uuid4(), "TARGET_PRICE", "1000@t", "EMAIL"),
        idempotency_key(user, entry, "MIN_DISCOUNT", "1000@t", "EMAIL"),
        idempotency_key(user, entry, "TARGET_PRICE", "900@t", "EMAIL"),
        idempotency_key(user, entry, "TARGET_PRICE", "1000@t", "WEB_PUSH"),
    }
    assert base not in variants and len(variants) == 5


def test_backoff_grows_exponentially_and_is_capped() -> None:
    for attempt, ceiling in ((1, 30), (2, 60), (3, 120), (10, 3600)):
        for _ in range(20):
            delay = backoff_seconds(attempt, 30, 3600)
            assert ceiling / 2 <= delay <= ceiling


async def test_lock_is_exclusive_and_released() -> None:
    kv = MemoryKV()
    async with distributed_lock(kv, "job") as first:
        assert first
        async with distributed_lock(kv, "job") as second:
            assert not second
    async with distributed_lock(kv, "job") as third:
        assert third


async def test_rate_limit_counter_increments_per_identity() -> None:
    kv = MemoryKV()
    assert [await hit_rate_limit(kv, "s", "a", 60) for _ in range(3)] == [1, 2, 3]
    assert await hit_rate_limit(kv, "s", "b", 60) == 1
