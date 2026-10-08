"""Time helpers. Timestamps are stored in UTC; user preferences use IANA zone names."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetimes are not accepted; supply an explicit offset")
    return value.astimezone(UTC)


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ValueError(f"Unknown IANA timezone: {name!r}") from exc
    return name


def in_quiet_hours(now_utc: datetime, start: time | None, end: time | None, tz_name: str) -> bool:
    if start is None or end is None or start == end:
        return False
    local = now_utc.astimezone(ZoneInfo(tz_name)).time()
    if start < end:
        return start <= local < end
    return local >= start or local < end  # window wraps past midnight


def quiet_hours_end(
    now_utc: datetime, start: time | None, end: time | None, tz_name: str
) -> datetime:
    """UTC instant at which the current quiet window ends (``now_utc`` if not quiet)."""
    if not in_quiet_hours(now_utc, start, end, tz_name) or end is None:
        return now_utc
    zone = ZoneInfo(tz_name)
    local_now = now_utc.astimezone(zone)
    candidate = local_now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)
