import pytest

from ledger.normalize import (InvalidValue, ist_date, ist_iso, normalise_header, normalise_phone,
                              parse_date, parse_decimal, parse_enum, parse_timestamp, to_epoch_us)


# ------------------------------------------------------------------ phone
@pytest.mark.parametrize("raw", [
    "9876543210", "+91 98765 43210", "09876543210", "91-98765-43210", "(+91) 9876543210",
    "+919876543210", " 98765-43210 ", "919876543210",
])
def test_phone_all_generator_formats(raw):
    assert normalise_phone(raw) == "+919876543210"


@pytest.mark.parametrize("raw", [
    "987654321",          # 9 digits
    "5876543210",         # bad leading digit
    "022-2345-6789",      # landline (11 digits starting 0 -> 10 digits starting 2)
    "N/A",
    "+1 415 555 0134",    # 11 digits starting 1
    "00919876543210",     # 14 digits: only one prefix is stripped
])
def test_phone_invalid(raw):
    with pytest.raises(InvalidValue):
        normalise_phone(raw)


def test_ten_digits_starting_91_is_a_plain_mobile():
    assert normalise_phone("9198765432") == "+919198765432"


def test_phone_12_digits_not_starting_91_is_invalid():
    with pytest.raises(InvalidValue):
        normalise_phone("929876543210")


def test_phone_prefix_stripped_then_validated():
    with pytest.raises(InvalidValue):
        normalise_phone("915876543210")  # 91 + number starting with 5
    with pytest.raises(InvalidValue):
        normalise_phone("05876543210")


# ------------------------------------------------------------------ dates
@pytest.mark.parametrize("raw,expected", [
    ("2026-12-01", "2026-12-01"),
    ("01/12/2026", "2026-12-01"),   # day-first
    ("01-12-2026", "2026-12-01"),
    ("01-Dec-2026", "2026-12-01"),
    ("01-DEC-2026", "2026-12-01"),
    ("01-dec-2026", "2026-12-01"),
    ("29/02/2028", "2028-02-29"),   # leap year
    ("5/3/2026", "2026-03-05"),
])
def test_dates_valid(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", [
    "31/02/2026", "29/02/2026", "2026-13-01", "soon", "TBD", "12/31/2026",
    "01-Dex-2026", "2026/12/01", "01/12-2026", "",
])
def test_dates_invalid(raw):
    with pytest.raises(InvalidValue):
        parse_date(raw)


# ------------------------------------------------------------------ decimals
@pytest.mark.parametrize("raw,expected", [
    ("27554", "27554.00"),
    ("₹27,554", "27554.00"),
    ("₹ 1,23,456.789", "123456.79"),
    ("Rs. 24787", "24787.00"),
    ("Rs.24787", "24787.00"),
    ("rs 100", "100.00"),
    ("INR 25301.4", "25301.40"),
    ("inr25301.4", "25301.40"),
    ("0", "0.00"),
    ("2.005", "2.01"),               # half-up, exact decimal (float would give 2.00)
    ("12.", "12.00"),
])
def test_decimal_valid(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["N/A", "-", "twelve thousand", "-5", "₹-5", "Rs", "1.2.3",
                                 "USD 5", "5 INR"])
def test_decimal_invalid(raw):
    with pytest.raises(InvalidValue):
        parse_decimal(raw)


# ------------------------------------------------------------------ enum / headers
def test_enum_case_insensitive_and_lowercased():
    assert parse_enum("HI", ["en", "hi"]) == "hi"
    with pytest.raises(InvalidValue):
        parse_enum("Hindi", ["en", "hi"])


@pytest.mark.parametrize("raw,expected", [
    ("Mobile No", "mobile no"), ("  mobile__no ", "mobile no"), ("Mobile _ No", "mobile no"),
    ("phone_number", "phone number"), ("Insured\tName", "insured name"),
])
def test_header_normalisation(raw, expected):
    assert normalise_header(raw) == expected


# ------------------------------------------------------------------ timestamps
def test_three_timestamp_forms_are_the_same_instant():
    utc = to_epoch_us(parse_timestamp("2026-10-03T04:31:00Z"))
    off = to_epoch_us(parse_timestamp("2026-10-03T10:01:00+05:30"))
    naive = to_epoch_us(parse_timestamp("2026-10-03 10:01:00"))  # naive == IST
    assert utc == off == naive


def test_ist_date_crosses_midnight():
    us = to_epoch_us(parse_timestamp("2026-10-01T19:00:00Z"))  # 00:30 IST next day
    assert ist_date(us) == "2026-10-02"
    assert ist_iso(us) == "2026-10-02T00:30:00+05:30"


def test_bad_timestamp():
    for raw in (None, "", "yesterday", 12345):
        with pytest.raises(InvalidValue):
            parse_timestamp(raw)
