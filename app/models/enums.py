from __future__ import annotations

from enum import StrEnum


class Channel(StrEnum):
    WEB_PUSH = "WEB_PUSH"
    EMAIL = "EMAIL"
    SMS = "SMS"


class DeliveryMode(StrEnum):
    IMMEDIATE = "IMMEDIATE"
    DIGEST = "DIGEST"


class OutboxState(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


class TriggerType(StrEnum):
    TARGET_PRICE = "TARGET_PRICE"
    MIN_DISCOUNT = "MIN_DISCOUNT"
    HISTORICAL_LOW = "HISTORICAL_LOW"
    BUY_TRANSITION = "BUY_TRANSITION"
    SALE_WINDOW_APPROACHING = "SALE_WINDOW_APPROACHING"
    TEST = "TEST"


class RecommendationAction(StrEnum):
    BUY = "BUY"
    WAIT = "WAIT"
    NEUTRAL = "NEUTRAL"


class ModelStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class DataQuality(StrEnum):
    GOOD = "GOOD"
    LIMITED = "LIMITED"
    INSUFFICIENT = "INSUFFICIENT"


class ForecastMethod(StrEnum):
    BASELINE = "BASELINE"
    ML = "ML"


def sql_in(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"
