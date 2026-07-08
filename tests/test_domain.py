"""Доменная логика (Эпик 4.3): канонизация связей, история паролей, сроки."""
from datetime import date, timedelta

from hranilka import domain


def test_canonical_link_pair_orders_min_max():
    assert domain.canonical_link_pair(5, 2) == (2, 5)
    assert domain.canonical_link_pair(2, 5) == (2, 5)


def test_days_until_password_change_basic():
    today = date.today()
    changed = (today - timedelta(days=10)).strftime("%Y-%m-%d")
    # сменён 10 дней назад, интервал 30 → осталось 20
    assert domain.days_until_password_change(changed, 30) == 20
    # просрочено
    assert domain.days_until_password_change(changed, 5) == -5


def test_days_until_password_change_none_cases():
    assert domain.days_until_password_change(None, 30) is None
    assert domain.days_until_password_change("2020-01-01", None) is None
    assert domain.days_until_password_change("не дата", 30) is None


def test_password_history_line_skips_unchanged_or_empty():
    assert domain.password_history_line("", "new", "2026-01-01") is None
    assert domain.password_history_line("same", "same", "2026-01-01") is None


def test_password_history_line_records_change():
    line = domain.password_history_line("old", "new", "2026-06-28 10:00:00")
    assert line == "Пароль old изменен: 2026-06-28 10:00:00"


def test_append_password_history_appends_and_preserves():
    # пустые заметки → только строка истории
    assert domain.append_password_history("", "old", "new", "T") == "Пароль old изменен: T"
    # непустые заметки → дописываем с новой строки
    assert domain.append_password_history("note", "old", "new", "T") == "note\nПароль old изменен: T"
    # без изменения пароля заметки не трогаются
    assert domain.append_password_history("note", "same", "same", "T") == "note"
