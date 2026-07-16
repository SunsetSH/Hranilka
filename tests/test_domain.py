"""Доменная логика (Эпик 4.3): канонизация связей, история паролей, сроки."""
from datetime import date, timedelta

from hranilka.core import domain


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


# ─── История паролей сервера: пользователи ОС / панели (§8.3) ──────────────

def test_os_user_password_history_lines_records_change_by_login():
    old = [{"login": "root", "password": "old1"}]
    new = [{"login": "root", "password": "new1"}]
    assert domain.os_user_password_history_lines(old, new, "T") == [
        "Пароль old1 от пользователя root изменен: T"]


def test_os_user_password_history_lines_format():
    old = [{"login": "root", "password": "hunter2"}]
    new = [{"login": "root", "password": "hunter3"}]
    [line] = domain.os_user_password_history_lines(old, new, "2026-06-28 10:00:00")
    assert line == "Пароль hunter2 от пользователя root изменен: 2026-06-28 10:00:00"


def test_panel_password_history_lines_records_change_by_type_and_login():
    old = [{"panel_type": "3x-ui", "login": "admin", "password": "old1"}]
    new = [{"panel_type": "3x-ui", "login": "admin", "password": "new1"}]
    assert domain.panel_password_history_lines(old, new, "T") == [
        "Пароль old1 от панели 3x-ui с логином admin изменен: T"]


def test_panel_password_history_lines_format():
    old = [{"panel_type": "Hestia", "login": "admin", "password": "p1"}]
    new = [{"panel_type": "Hestia", "login": "admin", "password": "p2"}]
    [line] = domain.panel_password_history_lines(old, new, "2026-06-28 10:00:00")
    assert line == "Пароль p1 от панели Hestia с логином admin изменен: 2026-06-28 10:00:00"


def test_os_user_password_history_new_row_no_line():
    # Логин новой строки не совпадает ни с одной старой -> пары нет, не пишем.
    old = [{"login": "root", "password": "old1"}]
    new = [{"login": "root", "password": "old1"},
          {"login": "deploy", "password": "brand-new"}]
    assert domain.os_user_password_history_lines(old, new, "T") == []


def test_os_user_password_history_login_and_password_change_treated_as_new():
    old = [{"login": "root", "password": "old1"}]
    new = [{"login": "admin", "password": "new1"}]
    assert domain.os_user_password_history_lines(old, new, "T") == []


def test_os_user_password_history_empty_old_password_no_line():
    old = [{"login": "root", "password": ""}]
    new = [{"login": "root", "password": "new1"}]
    assert domain.os_user_password_history_lines(old, new, "T") == []


def test_os_user_password_history_unchanged_no_line():
    old = [{"login": "root", "password": "same"}]
    new = [{"login": "root", "password": "same"}]
    assert domain.os_user_password_history_lines(old, new, "T") == []


# ─── Пустой логин — полноправный ключ сопоставления (§8) ───────────────────

def test_os_user_password_history_empty_login_records_change():
    old = [{"login": "", "password": "old1"}]
    new = [{"login": "", "password": "new1"}]
    assert domain.os_user_password_history_lines(old, new, "T") == [
        "Пароль old1 от пользователя без логина изменен: T"]


def test_os_user_password_history_empty_login_duplicates_matched_in_order():
    old = [{"login": "", "password": "old1"}, {"login": "", "password": "old2"}]
    new = [{"login": "", "password": "new1"}, {"login": "", "password": "new2"}]
    assert domain.os_user_password_history_lines(old, new, "T") == [
        "Пароль old1 от пользователя без логина изменен: T",
        "Пароль old2 от пользователя без логина изменен: T"]


def test_panel_password_history_empty_login_records_change():
    old = [{"panel_type": "3x-ui", "login": "", "password": "old1"}]
    new = [{"panel_type": "3x-ui", "login": "", "password": "new1"}]
    assert domain.panel_password_history_lines(old, new, "T") == [
        "Пароль old1 от панели 3x-ui без логина изменен: T"]


def test_panel_password_history_empty_login_duplicates_matched_in_order():
    old = [{"panel_type": "3x-ui", "login": "", "password": "old1"},
          {"panel_type": "3x-ui", "login": "", "password": "old2"}]
    new = [{"panel_type": "3x-ui", "login": "", "password": "new1"},
          {"panel_type": "3x-ui", "login": "", "password": "new2"}]
    assert domain.panel_password_history_lines(old, new, "T") == [
        "Пароль old1 от панели 3x-ui без логина изменен: T",
        "Пароль old2 от панели 3x-ui без логина изменен: T"]


def test_append_server_password_history_combines_both_and_multiple_lines():
    old_payload = {
        "os_users": [{"login": "root", "password": "u-old"}],
        "panels": [{"panel_type": "3x-ui", "login": "admin", "password": "p-old"}],
    }
    new_payload = {
        "os_users": [{"login": "root", "password": "u-new"}],
        "panels": [{"panel_type": "3x-ui", "login": "admin", "password": "p-new"}],
    }
    updated = domain.append_server_password_history(
        "заметка", old_payload, new_payload, "T")
    assert updated == (
        "заметка\n"
        "Пароль u-old от пользователя root изменен: T\n"
        "Пароль p-old от панели 3x-ui с логином admin изменен: T")
