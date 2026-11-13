"""Pure value-normalisation rules from the spec. No I/O, no DB: easy to test.

Every parser returns the canonical value, or raises ``InvalidValue``.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

IST = timezone(timedelta(hours=5, minutes=30), "IST")


class InvalidValue(ValueError):
    pass


# ---------------------------------------------------------------- headers
_HEADER_SEP = re.compile(r"[\s_]+")


def normalise_header(h: str) -> str:
    """lower-case, trim, collapse every run of '_' / whitespace to one space."""
    return _HEADER_SEP.sub(" ", h.strip().lower()).strip()


# ---------------------------------------------------------------- phone
_NON_DIGIT = re.compile(r"\D")


def normalise_phone(raw: str) -> str:
    """Indian mobile -> '+91XXXXXXXXXX'. Raises InvalidValue."""
    digits = _NON_DIGIT.sub("", raw)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        raise InvalidValue(f"invalid phone")
    return "+91" + digits


# ---------------------------------------------------------------- date
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DMY = re.compile(r"^(\d{1,2})([/-])(\d{1,2})\2(\d{4})$")  # same separator both times
_DMONY = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$")


def parse_date(raw: str) -> str:
    """Accepts YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, DD-Mon-YYYY. Returns ISO 'YYYY-MM-DD'."""
    s = raw.strip()
    if m := _ISO.match(s):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    elif m := _DMY.match(s):
        d, mo, y = int(m[1]), int(m[3]), int(m[4])
    elif m := _DMONY.match(s):
        mon = _MONTHS.get(m[2].lower())
        if mon is None:
            raise InvalidValue("invalid month name")
        d, mo, y = int(m[1]), mon, int(m[3])
    else:
        raise InvalidValue("unrecognised date format")
    try:
        return date(y, mo, d).isoformat()
    except ValueError as e:  # 31/02, month 13, ...
        raise InvalidValue(str(e)) from None


# ---------------------------------------------------------------- decimal
_CURRENCY_PREFIX = re.compile(r"^(?:₹|rs\.?|inr)\s*", re.IGNORECASE)
_NUMBER = re.compile(r"^(?:\d+(?:\.\d*)?|\.\d+)$")
_CENT = Decimal("0.01")


def parse_decimal(raw: str) -> str:
    """Optional ₹ / Rs / Rs. / INR prefix, ',' thousands separators, >= 0, 2 dp.

    Returned as a canonical string ('1234.50') so it round-trips exactly.
    """
    s = _CURRENCY_PREFIX.sub("", raw.strip(), count=1).replace(",", "").strip()
    if not _NUMBER.match(s):  # rejects '-', '-5', 'N/A', 'twelve thousand', ''
        raise InvalidValue("not a non-negative number")
    try:
        value = Decimal(s)
    except InvalidOperation:  # pragma: no cover - regex already guards
        raise InvalidValue("not a number") from None
    return str(value.quantize(_CENT, rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------- enum
def parse_enum(raw: str, values: list[str]) -> str:
    v = raw.strip().lower()
    if v not in {x.lower() for x in values}:
        raise InvalidValue("not an allowed value")
    return v


# ---------------------------------------------------------------- timestamps
def parse_timestamp(raw: str) -> datetime:
    """'...Z', '...+05:30' or naive (naive == IST). Returns an aware datetime."""
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidValue("missing timestamp")
    try:
        dt = datetime.fromisoformat(raw.strip())
    except ValueError:
        raise InvalidValue("unparseable timestamp") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def to_epoch_us(dt: datetime) -> int:
    """Exact integer microseconds since epoch: sortable, no float drift."""
    delta = dt - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def from_epoch_us(us: int) -> datetime:
    return _EPOCH + timedelta(microseconds=us)


def ist_iso(us: int) -> str:
    """ISO-8601 in IST, e.g. 2026-10-14T17:55:24+05:30."""
    return from_epoch_us(us).astimezone(IST).isoformat()


def ist_date(us: int) -> str:
    return from_epoch_us(us).astimezone(IST).date().isoformat()
