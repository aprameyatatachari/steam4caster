"""Structured JSON logging with correlation IDs and secret/PII redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_SENSITIVE_KEYS = re.compile(
    r"(pass(word)?|secret|token|authorization|api[_-]?key|p256dh|auth_key|cookie|"
    r"endpoint|phone|vapid|smtp_password|refresh|jwt)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b")
_KEY_PARAM = re.compile(r"([?&](?:key|token|api_key)=)[^&\s]+", re.IGNORECASE)
_PHONE = re.compile(r"\+\d{7,15}\b")

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def redact_text(text: str) -> str:
    text = _BEARER.sub(r"\1[REDACTED]", text)
    text = _JWT.sub("[REDACTED_JWT]", text)
    text = _KEY_PARAM.sub(r"\1[REDACTED]", text)
    text = _EMAIL.sub(r"\1***@\2", text)
    return _PHONE.sub("[REDACTED_PHONE]", text)


def redact(value: Any, key: str | None = None) -> Any:
    """Recursively redact sensitive values from log payloads."""
    if key is not None and _SENSITIVE_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return redact_text(str(value))


def mask_endpoint(endpoint: str) -> str:
    """Return a loggable form of a push endpoint (host + short suffix only)."""
    match = re.match(r"https?://([^/]+)/", endpoint)
    host = match.group(1) if match else "unknown"
    return f"{host}/…{endpoint[-6:]}" if len(endpoint) > 6 else host


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        correlation_id = correlation_id_var.get()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = redact(value, key)
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = {
            k: redact(v, k)
            for k, v in record.__dict__.items()
            if k not in _RESERVED and not k.startswith("_")
        }
        base = f"{record.levelname:<7} {record.name}: {redact_text(record.getMessage())}"
        if extras:
            base += f" {extras}"
        if record.exc_info:
            base += "\n" + redact_text(self.formatException(record.exc_info))
        return base


def configure_logging(
    level: str = "INFO", json_logs: bool = True, stream: TextIO | None = None
) -> None:
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter() if json_logs else PlainFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
