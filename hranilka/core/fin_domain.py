"""Финансовая доменная логика — чистая, без SQL и без Qt (по образцу
core/domain.py). Алгоритмы банковских карт: контрольная сумма Луна,
BIN-детект платёжной системы, разбор срока действия, маскирование.

Всё детерминировано и тестируется в изоляции (см. tests/test_fin_domain.py).
"""
import calendar
import re
from datetime import date, datetime
from typing import Optional

# Порог предупреждения об истечении карты (дней): маркер [!] в дереве, плашка
# на карточке, подсветка ExpiryField. Позже — вынести в настройки (концепт §10.3).
EXPIRY_WARN_DAYS = 30


def normalize_card_number(s: str) -> str:
    """Убирает пробелы и дефисы из номера карты (хранение/копирование — без них)."""
    if not s:
        return ""
    return s.replace(" ", "").replace("-", "")


def _digits(number: str) -> str:
    """Только цифры из строки (нецифры игнорируются)."""
    return "".join(c for c in (number or "") if c.isdigit())


def luhn_check(number: str) -> bool:
    """Контрольная сумма Луна. Нецифры игнорируются; при <12 цифрах — False
    (номер слишком короткий, проверять нечего). Только предупреждение в UI —
    ввод не блокируется (виртуальные/нестандартные карты существуют)."""
    digits = [int(c) for c in _digits(number)]
    if len(digits) < 12:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:          # каждая вторая цифра справа удваивается
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def detect_payment_system(number: str) -> str:
    """Платёжная система по BIN-префиксу (таблица по убыванию специфичности,
    концепт §10.1). Пустая строка — если ни один диапазон не подошёл."""
    digits = _digits(number)
    if not digits:
        return ""

    def pfx(n: int) -> Optional[int]:
        return int(digits[:n]) if len(digits) >= n else None

    p1, p2, p3, p4 = pfx(1), pfx(2), pfx(3), pfx(4)

    if p4 is not None and 2200 <= p4 <= 2204:
        return "Мир"
    if (p4 is not None and 2221 <= p4 <= 2720) or (p2 is not None and 51 <= p2 <= 55):
        return "Mastercard"
    if p1 == 4:
        return "Visa"
    if p2 in (34, 37):
        return "American Express"
    if p2 == 62:
        return "UnionPay"
    if p4 is not None and 3528 <= p4 <= 3589:
        return "JCB"
    if p2 == 50 or (p2 is not None and 56 <= p2 <= 58) or p3 == 639 or p2 == 67:
        return "Maestro"
    return ""


def parse_expiry(mm_yy: str) -> Optional[date]:
    """«MM/YY» → последний день указанного месяца (источник expires_on).
    None — если формат не распознан или месяц вне 1..12."""
    if not mm_yy:
        return None
    m = re.fullmatch(r"\s*(\d{1,2})\s*/\s*(\d{2}|\d{4})\s*", mm_yy)
    if not m:
        return None
    month = int(m.group(1))
    year = int(m.group(2))
    if not 1 <= month <= 12:
        return None
    if year < 100:                # двузначный год → 20YY
        year += 2000
    last_day = calendar.monthrange(year, month)[1]   # учитывает високосный февраль
    return date(year, month, last_day)


def days_until_expiry(expires_on) -> Optional[int]:
    """Сколько дней осталось до истечения (отрицательное — уже истекла).
    None — если срок не задан/некорректен. Принимает date или строку
    «ГГГГ-ММ-ДД» (экстракт-колонка expires_on приходит строкой)."""
    if not expires_on:
        return None
    if isinstance(expires_on, str):
        try:
            expires_on = datetime.strptime(expires_on[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
    return expires_on.toordinal() - date.today().toordinal()


def card_last4(number: str) -> str:
    """Последние 4 цифры номера (для поиска/отображения). Пустая строка,
    если цифр меньше четырёх."""
    digits = _digits(number)
    return digits[-4:] if len(digits) >= 4 else ""


def mask_card_number(number: str) -> str:
    """Маскированный номер «**** **** **** 1234». Пустая строка — если цифр
    меньше четырёх (нечего показывать)."""
    last4 = card_last4(number)
    if not last4:
        return ""
    return f"**** **** **** {last4}"
