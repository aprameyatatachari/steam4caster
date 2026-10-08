"""Opaque cursor pagination helpers (keyset on a timestamp plus a tiebreaker id)."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from app.core.errors import ValidationFailed


def encode_cursor(at: datetime, tiebreaker: Any) -> str:
    raw = json.dumps({"t": at.isoformat(), "i": str(tiebreaker)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        at = datetime.fromisoformat(data["t"])
        if at.tzinfo is None:
            raise ValueError("naive cursor timestamp")
        return at, str(data["i"])
    except Exception as exc:
        raise ValidationFailed("Invalid pagination cursor.") from exc
