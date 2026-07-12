"""Финансовая доменная логика (hranilka/core/fin_domain.py): Луна, BIN-детект,
разбор срока, маскирование/last4/нормализация. Чистые функции, без БД и Qt."""
from datetime import date

import pytest

from hranilka.core.fin_domain import (
    card_last4, days_until_expiry, detect_payment_system, luhn_check,
    mask_card_number, normalize_card_number, parse_expiry,
)


# ----- Луна -----

@pytest.mark.parametrize("number", [
    "4539148803436467",        # валидный Visa-подобный
    "4111 1111 1111 1111",     # валидный, с пробелами
    "5500-0000-0000-0004",     # валидный, с дефисами
    "79927398713",             # 11 цифр — но короче 12, см. отдельный тест ниже
])
def test_luhn_valid_where_long_enough(number):
    digits = [c for c in number if c.isdigit()]
    if len(digits) < 12:
        assert luhn_check(number) is False   # слишком короткий → всегда False
    else:
        assert luhn_check(number) is True


def test_luhn_ignores_non_digits():
    assert luhn_check("4111-1111 1111_1111") is True


def test_luhn_invalid_checksum():
    assert luhn_check("4111 1111 1111 1112") is False


def test_luhn_too_short_is_false():
    assert luhn_check("4111111111") is False    # 10 цифр < 12
    assert luhn_check("") is False
    assert luhn_check("abcd") is False


# ----- BIN-детект: каждый диапазон -----

@pytest.mark.parametrize("prefix,expected", [
    ("2202", "Мир"),
    ("2200", "Мир"),
    ("2204", "Мир"),
    ("5100", "Mastercard"),
    ("5500", "Mastercard"),
    ("2221", "Mastercard"),
    ("2720", "Mastercard"),
    ("4000", "Visa"),
    ("4", "Visa"),
    ("3400", "American Express"),
    ("3700", "American Express"),
    ("6200", "UnionPay"),
    ("3528", "JCB"),
    ("3589", "JCB"),
    ("5000", "Maestro"),
    ("5600", "Maestro"),
    ("5800", "Maestro"),
    ("6390", "Maestro"),
    ("6700", "Maestro"),
])
def test_detect_payment_system_ranges(prefix, expected):
    assert detect_payment_system(prefix) == expected


@pytest.mark.parametrize("prefix", ["2205", "2220", "2721", "9999", "1000", ""])
def test_detect_payment_system_unknown(prefix):
    assert detect_payment_system(prefix) == ""


def test_detect_payment_system_boundaries():
    # Границы диапазона «Мир» и Mastercard-2xxx
    assert detect_payment_system("2204") == "Мир"
    assert detect_payment_system("2205") == ""
    assert detect_payment_system("2220") == ""
    assert detect_payment_system("2221") == "Mastercard"
    assert detect_payment_system("2720") == "Mastercard"
    assert detect_payment_system("2721") == ""


def test_detect_payment_system_ignores_spaces():
    assert detect_payment_system("4111 1111 1111 1111") == "Visa"


# ----- Разбор срока действия -----

def test_parse_expiry_valid():
    assert parse_expiry("12/25") == date(2025, 12, 31)
    assert parse_expiry("01/30") == date(2030, 1, 31)


def test_parse_expiry_leap_february():
    assert parse_expiry("02/24") == date(2024, 2, 29)     # високосный
    assert parse_expiry("02/25") == date(2025, 2, 28)     # обычный


def test_parse_expiry_four_digit_year():
    assert parse_expiry("06/2027") == date(2027, 6, 30)


def test_parse_expiry_garbage():
    for bad in ["", "13/25", "00/25", "abcd", "12-25", "1225", "12/2"]:
        assert parse_expiry(bad) is None


# ----- days_until_expiry -----

def test_days_until_expiry_none():
    assert days_until_expiry(None) is None
    assert days_until_expiry("") is None
    assert days_until_expiry("мусор") is None


def test_days_until_expiry_counts():
    today = date.today()
    future = date(today.year + 1, today.month, today.day)
    assert days_until_expiry(future) > 0
    assert days_until_expiry(future.isoformat()) > 0     # строкой тоже


# ----- last4 / mask / normalize -----

def test_card_last4():
    assert card_last4("4111 1111 1111 1234") == "1234"
    assert card_last4("12") == ""
    assert card_last4("") == ""


def test_mask_card_number():
    assert mask_card_number("4111111111111234") == "**** **** **** 1234"
    assert mask_card_number("12") == ""


def test_normalize_card_number():
    assert normalize_card_number("4111 1111-1111 1234") == "4111111111111234"
    assert normalize_card_number("") == ""
