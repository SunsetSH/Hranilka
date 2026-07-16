"""Доменная (бизнес-) логика «Хранилки» — чистая, без SQL и без Qt.

Сюда вынесены правила, которые раньше были размазаны между слоем данных
(database.py) и UI (main.py): симметрия/канонизация связей, форматирование
истории паролей и расчёт срока смены пароля (Эпик 4). Всё детерминировано и
тестируется в изоляции, без БД и без виджетов."""
from datetime import datetime, date
from typing import Any, Optional


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


def days_until_paid_until(paid_until: Optional[str]) -> Optional[int]:
    """Сколько дней осталось до «оплачен до» VPS-сервера (может быть
    отрицательным, если срок уже прошёл). None — если значение не задано или
    не распознано как дата «ГГГГ-ММ-ДД» (paid_until — экстракт-колонка
    servers.paid_until; поле может быть и произвольным текстом, см.
    docs/ТЗ_VPS_Серверы.md §3 — тогда маркер/сортировка по нему не строятся).
    Независима от fin_domain.days_until_expiry — сервер не финансовый лист."""
    if not paid_until:
        return None
    try:
        due = datetime.strptime(str(paid_until)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return due.toordinal() - date.today().toordinal()


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


def _match_rows_by_key(old_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]],
                       key_fn) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Сопоставляет строки old_rows/new_rows один-к-одному по key_fn (строки
    списков сервера — пользователи ОС/панели, docs/ТЗ_VPS_Серверы.md §8.3):
    каждая old-строка участвует не более чем в одном соответствии, порядок
    появления учитывается при повторяющихся ключах. key_fn(row) может
    вернуть None — такая строка (например, без логина) в сопоставлении не
    участвует. Возвращает пары (old_row, new_row)."""
    buckets: dict[Any, list[dict[str, Any]]] = {}
    for row in old_rows:
        k = key_fn(row)
        if k is not None:
            buckets.setdefault(k, []).append(row)
    pairs = []
    for row in new_rows:
        k = key_fn(row)
        if k is None:
            continue
        bucket = buckets.get(k)
        if bucket:
            pairs.append((bucket.pop(0), row))
    return pairs


def os_user_password_history_lines(old_users: list[dict[str, Any]],
                                   new_users: list[dict[str, Any]],
                                   stamp: str) -> list[str]:
    """Строки истории смены пароля пользователей ОС сервера (docs/ТЗ_VPS_
    Серверы.md §8.3, по образцу password_history_line для аккаунта).

    Сопоставление old↔new — по login (устойчиво к добавлению/удалению/
    перестановке строк списка). Пустой логин — полноправный ключ сопоставления
    (пустая строка равна пустой строке, а не "нет ключа") — иначе смена пароля
    у пользователя без логина молча не записывалась бы; в тексте сообщения
    пустой логин заменяется на «без логина». Если у строки одновременно
    сменились и логин, и пароль — она не находит пару по старому логину и
    считается новой записью: ничего не дописывается (это же правило покрывает
    добавление строки). Пустой старый пароль — писать нечего."""
    lines = []
    pairs = _match_rows_by_key(
        old_users, new_users, lambda u: str(u.get("login") or ""))
    for old_u, new_u in pairs:
        old_pw = str(old_u.get("password") or "")
        new_pw = str(new_u.get("password") or "")
        if old_pw and new_pw != old_pw:
            login = str(new_u.get("login") or "")
            who = f"пользователя {login}" if login else "пользователя без логина"
            lines.append(f"Пароль {old_pw} от {who} изменен: {stamp}")
    return lines


def panel_password_history_lines(old_panels: list[dict[str, Any]],
                                 new_panels: list[dict[str, Any]],
                                 stamp: str) -> list[str]:
    """Строки истории смены пароля панелей управления сервера — по образцу
    os_user_password_history_lines. Сопоставление old↔new — по паре
    (panel_type, login); пустой логин (и/или пустой panel_type) — полноправная
    часть ключа (пустая строка равна пустой строке), а не «нет ключа» — иначе
    смена пароля у панели без логина молча не записывалась бы. В тексте
    сообщения пустой логин заменяется на «без логина»."""
    lines = []
    pairs = _match_rows_by_key(
        old_panels, new_panels,
        lambda p: (str(p.get("panel_type") or ""), str(p.get("login") or "")))
    for old_p, new_p in pairs:
        old_pw = str(old_p.get("password") or "")
        new_pw = str(new_p.get("password") or "")
        if old_pw and new_pw != old_pw:
            panel_type = str(new_p.get("panel_type") or "")
            login = str(new_p.get("login") or "")
            if login:
                lines.append(
                    f"Пароль {old_pw} от панели {panel_type} с логином {login} "
                    f"изменен: {stamp}")
            else:
                lines.append(
                    f"Пароль {old_pw} от панели {panel_type} без логина "
                    f"изменен: {stamp}")
    return lines


def append_password_history_lines(notes: str, lines: list[str]) -> str:
    """Дописать несколько строк истории в конец заметок, каждую с новой
    строки (несколько смен пароля за одно сохранение — несколько строк)."""
    text = notes
    for line in lines:
        text = (text + "\n" + line) if text else line
    return text


def append_server_password_history(notes: str, old_payload: dict[str, Any],
                                   new_payload: dict[str, Any],
                                   stamp: str) -> str:
    """Дописать в заметки сервера строки истории смены пароля пользователей
    ОС и панелей управления (docs/ТЗ_VPS_Серверы.md §8.3). old_payload/
    new_payload — payload сервера до/после правок текущего сохранения
    (os_users/panels — списки словарей строк)."""
    lines = os_user_password_history_lines(
        old_payload.get("os_users") or [], new_payload.get("os_users") or [], stamp)
    lines += panel_password_history_lines(
        old_payload.get("panels") or [], new_payload.get("panels") or [], stamp)
    return append_password_history_lines(notes, lines)
