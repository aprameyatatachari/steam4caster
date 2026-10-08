"""Money helpers. Stored money is always integer minor units plus an ISO 4217 code."""

from __future__ import annotations

import re
from decimal import Decimal

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

# ISO 4217 exponents that differ from the default of 2.
_EXPONENTS: dict[str, int] = {
    "BIF": 0, "CLP": 0, "DJF": 0, "GNF": 0, "ISK": 0, "JPY": 0, "KMF": 0, "KRW": 0,
    "PYG": 0, "RWF": 0, "UGX": 0, "UYI": 0, "VND": 0, "VUV": 0, "XAF": 0, "XOF": 0,
    "XPF": 0, "BHD": 3, "IQD": 3, "JOD": 3, "KWD": 3, "LYD": 3, "OMR": 3, "TND": 3,
}  # fmt: skip


class CurrencyMismatch(ValueError):
    pass


def normalize_currency(code: str) -> str:
    code = code.strip().upper()
    if not _CURRENCY_RE.match(code):
        raise ValueError(f"Invalid ISO 4217 currency code: {code!r}")
    return code


def normalize_country(code: str) -> str:
    code = code.strip().upper()
    if not _COUNTRY_RE.match(code):
        raise ValueError(f"Invalid ISO 3166-1 alpha-2 country code: {code!r}")
    return code


def currency_exponent(currency: str) -> int:
    return _EXPONENTS.get(currency.upper(), 2)


def minor_to_decimal(amount_minor: int, currency: str) -> Decimal:
    return Decimal(amount_minor).scaleb(-currency_exponent(currency))


def format_minor(amount_minor: int, currency: str) -> str:
    """Decimal string for display, e.g. 1999/USD -> '19.99'. Never a binary float."""
    exponent = currency_exponent(currency)
    return f"{minor_to_decimal(amount_minor, currency):.{exponent}f}"


def ensure_same_currency(*currencies: str | None) -> str | None:
    """Return the common currency of the non-null inputs or raise CurrencyMismatch."""
    present = {c for c in currencies if c}
    if len(present) > 1:
        raise CurrencyMismatch(f"Mixed currencies are not comparable: {sorted(present)}")
    return next(iter(present), None)


def discount_pct(price_minor: int, regular_minor: int) -> int:
    if regular_minor <= 0 or price_minor >= regular_minor:
        return 0
    return round((regular_minor - price_minor) * 100 / regular_minor)


def apply_discount(regular_minor: int, discount_fraction: float) -> int:
    """Price in minor units after a fractional discount, clamped to [0, regular]."""
    fraction = min(max(discount_fraction, 0.0), 1.0)
    return max(0, min(regular_minor, round(regular_minor * (1.0 - fraction))))
