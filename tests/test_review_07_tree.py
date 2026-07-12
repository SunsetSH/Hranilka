"""M7-04: атомарность составных команд дерева (delete_items, move_*_to_new_*,
batch-перемещение/избранное, add_account_with_card).

Проверяем: составной метод — ОДНА транзакция, при сбое любого шага БД не
меняется (полный откат), а на успехе — согласованный результат."""
import pytest


def _card():
    """Минимальная карточка в формате _save_account_rows."""
    return {
        "fields": {
            "account_name": "A", "url": None, "login": None, "password": None,
            "creation_date": None, "password_changed_date": None,
            "password_change_interval_days": None, "notes": None, "ip": None,
            "browser": None, "os": None, "extra_info": None,
        },
        "personal": {"mobile_phone": None, "first_name": None, "last_name": None,
                     "middle_name": None, "birth_date": None, "address": None},
        "questions": [],
        "recovery": {"phrase": "", "device_id": ""},
        "codes": [],
        "gallery": [],
    }


def _count(db, table, where="1", params=()):
    db.cursor.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params)
    return db.cursor.fetchone()["n"]


# ─── delete_items ─────────────────────────────────────────────────────────────

def test_delete_items_mixed_atomic(db):
    """Смешанный набор удаляется атомарно; возвращены типизированные ключи
    всех затронутых листьев (потомки папки + сам аккаунт)."""
    fid = db.add_folder("F")
    sid = db.add_service("S", fid)
    a1 = db.add_account(sid, "A1")
    a2 = db.add_account(sid, "A2")
    free = db.add_account(None, "Free")

    affected = db.delete_items([("folder", fid), ("account", free)])

    assert set(affected) == {("account", a1), ("account", a2),
                             ("account", free)}
    assert _count(db, "folders") == 0
    assert _count(db, "services") == 0
    assert _count(db, "accounts") == 0


def test_delete_items_to_bin_soft(db):
    """to_bin=True: аккаунты не удаляются, а помечаются удалёнными (корзина)."""
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")

    affected = db.delete_items([("account", a1)], to_bin=True)

    assert affected == [("account", a1)]
    assert _count(db, "accounts") == 1                       # строка на месте
    assert _count(db, "accounts", "deleted_at IS NOT NULL") == 1   # в корзине


def test_delete_items_keep_content(db):
    """keep=True: контейнер удалён, содержимое поднято на уровень выше,
    аккаунты не затронуты (affected пуст)."""
    fid = db.add_folder("F")
    sid = db.add_service("S", fid)
    a1 = db.add_account(sid, "A1")

    affected = db.delete_items([("folder", fid)], keep=True)

    assert affected == []
    assert _count(db, "folders") == 0
    assert _count(db, "services", "id = ?", (sid,)) == 1     # сервис выжил
    db.cursor.execute("SELECT folder_id FROM services WHERE id = ?", (sid,))
    assert db.cursor.fetchone()["folder_id"] is None         # стал вне папки
    assert _count(db, "accounts", "id = ?", (a1,)) == 1


def test_delete_items_container_returns_fin_leaf_keys(db):
    """Каскадно удаляемые fin-листья возвращаются для очистки UI-черновиков."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    card = db.add_fin_item(sid, "bank_card", "Карта")
    wallet = db.add_fin_item(sid, "crypto_wallet", "Кошелёк")

    affected = db.delete_items([("service", sid)])

    assert set(affected) == {("account", aid), ("card", card),
                             ("wallet", wallet)}


def test_delete_items_rollback_on_midway_error(db, monkeypatch):
    """Сбой на шаге удаления второго узла откатывает ВЕСЬ набор: первый
    (папка) тоже остаётся на месте — единая транзакция."""
    fid = db.add_folder("F")
    sid = db.add_service("S", fid)
    db.add_account(sid, "A1")
    free = db.add_account(None, "Free")

    def boom(_account_id):
        raise RuntimeError("сбой на аккаунте")

    monkeypatch.setattr(db, "_delete_account_rows", boom)

    with pytest.raises(RuntimeError):
        db.delete_items([("folder", fid), ("account", free)])

    # Ничего не удалено — откат транзакции.
    assert _count(db, "folders", "id = ?", (fid,)) == 1
    assert _count(db, "services", "id = ?", (sid,)) == 1
    assert _count(db, "accounts", "id = ?", (free,)) == 1


# ─── move_services_to_new_folder / move_accounts_to_new_service ───────────────

def test_move_services_to_new_folder_happy(db):
    s1 = db.add_service("S1")
    s2 = db.add_service("S2")

    fid = db.move_services_to_new_folder("Nova", [s1, s2])

    assert _count(db, "folders", "id = ?", (fid,)) == 1
    assert _count(db, "services", "folder_id = ?", (fid,)) == 2


def test_move_services_to_new_folder_rollback(db, monkeypatch):
    """Сбой при переносе 2-го сервиса → папка не создана, ни один сервис не
    перемещён (единая транзакция)."""
    s1 = db.add_service("S1")
    s2 = db.add_service("S2")
    folders_before = _count(db, "folders")

    calls = {"n": 0}
    orig = db._move_service_rows

    def flaky(service_id, folder_id):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("сбой на втором сервисе")
        return orig(service_id, folder_id)

    monkeypatch.setattr(db, "_move_service_rows", flaky)

    with pytest.raises(RuntimeError):
        db.move_services_to_new_folder("Nova", [s1, s2])

    assert _count(db, "folders") == folders_before           # папки не прибавилось
    assert _count(db, "services", "folder_id IS NOT NULL") == 0


def test_move_accounts_to_new_service_happy(db):
    a1 = db.add_account(None, "A1")
    a2 = db.add_account(None, "A2")

    sid = db.move_accounts_to_new_service("Svc", [a1, a2])

    assert _count(db, "services", "id = ?", (sid,)) == 1
    assert _count(db, "accounts", "service_id = ?", (sid,)) == 2


# ─── batch move / favorites ───────────────────────────────────────────────────

def test_set_favorites_batch(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    a2 = db.add_account(sid, "A2")

    db.set_favorites([a1, a2], True)
    assert _count(db, "accounts", "is_favorite = 1") == 2

    db.set_favorites([a1], False)
    assert _count(db, "accounts", "is_favorite = 1") == 1


def test_move_accounts_batch(db):
    s1 = db.add_service("S1")
    s2 = db.add_service("S2")
    a1 = db.add_account(s1, "A1")
    a2 = db.add_account(s1, "A2")

    db.move_accounts([a1, a2], s2)
    assert _count(db, "accounts", "service_id = ?", (s2,)) == 2


def test_move_services_batch(db):
    f1 = db.add_folder("F1")
    s1 = db.add_service("S1")
    s2 = db.add_service("S2")

    db.move_services([s1, s2], f1)
    assert _count(db, "services", "folder_id = ?", (f1,)) == 2


# ─── add_account_with_card ────────────────────────────────────────────────────

def test_add_account_with_card_happy(db):
    sid = db.add_service("S")
    aid = db.add_account_with_card(sid, "Acc", _card())

    assert _count(db, "accounts", "id = ?", (aid,)) == 1
    db.cursor.execute("SELECT service_id FROM accounts WHERE id = ?", (aid,))
    assert db.cursor.fetchone()["service_id"] == sid


def test_add_account_with_card_rollback(db, monkeypatch):
    """Сбой записи карточки откатывает INSERT аккаунта — полупустой строки нет."""
    sid = db.add_service("S")
    before = _count(db, "accounts")

    def boom(_account_id, _data):
        raise RuntimeError("сбой карточки")

    monkeypatch.setattr(db, "_save_account_rows", boom)

    with pytest.raises(RuntimeError):
        db.add_account_with_card(sid, "Acc", _card())

    assert _count(db, "accounts") == before                  # аккаунт не создан
