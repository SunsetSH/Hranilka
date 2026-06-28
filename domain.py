"""Доменная (бизнес-) логика «Хранилки» — чистая, без SQL и без Qt.

Сюда вынесены правила, которые раньше были размазаны между слоем данных
(database.py) и UI (main.py): симметрия/канонизация связей, форматирование
истории паролей и расчёт срока смены пароля (Эпик 4). Всё детерминировано и
тестируется в изоляции, без БД и без виджетов."""
from datetime import datetime, date
from typing import Optional


def canonical_link_pair(a: int, b: int) -> tuple[int, int]:
    """Каноническая (упорядоченная) пара связи: (min, max).

    Связь симметрична, поэтому (A,B) и (B,A) — одно и то же. Храним всегда в
    виде account_id < linked_account_id (этот инвариант закреплён CHECK в схеме,
    см. database._linked_accounts_create_sql)."""
    return (a, b) if a < b else (b, a)


def days_until_password_change(changed_date_str, interval_days) -> Optional[int]:
    """Сколько дней осталось до смены пароля (может быть отрицательным, если срок
    уже прошёл). None — если срок не задан или дата некорректна."""
    if not changed_date_str or not interval_days:
        return None
    try:
        changed = datetime.strptime(str(changed_date_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    due = changed.toordinal() + int(interval_days)
    return due - date.today().toordinal()


def password_history_line(old_password: str, new_password: str,
                          stamp: str) -> Optional[str]:
    """Строка истории смены пароля или None, если записывать нечего.

    Пишем, только если старый пароль был непустым и реально изменился —
    тогда сохраняем сам прежний пароль и момент смены."""
    if not old_password or new_password == old_password:
        return None
    return f"Пароль {old_password} изменен: {stamp}"


def append_password_history(notes: str, old_password: str, new_password: str,
                            stamp: str) -> str:
    """Дописать строку истории смены пароля в конец заметок (если пароль
    изменился). Возвращает итоговый текст заметок (без изменений, если писать
    нечего). UI-побочки (обновление поля) остаются на стороне вызывающего."""
    line = password_history_line(old_password, new_password, stamp)
    if line is None:
        return notes
    return (notes + "\n" + line) if notes else line
