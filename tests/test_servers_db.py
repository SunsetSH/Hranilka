"""CRUD VPS-серверов (DbServersMixin): payload round-trip (os_users/ssh_keys/
panels), экстракт-колонка paid_until, связи с аккаунтами, корзина, избранное,
порядок, галерея. Плюс модель ServerData.

Сервер — независимая от fin_items сущность (docs/ТЗ_VPS_Серверы.md §2): тесты
не переиспользуют fin-фикстуры/хелперы.
"""
import pytest

from hranilka.data.models.server_data import ServerData, extract_paid_until


# ----- Модель ServerData -----

def test_server_data_roundtrip():
    d = ServerData("VPS-1")
    d.payload = {
        "hosting": "Aeza", "host": "203.0.113.10", "ssh_port": "22",
        "extra_ips": ["10.0.0.5", "2a01:4f8::1"], "os_name": "Ubuntu 24.04",
        "location": "Франкфурт", "paid_until": "2026-08-01", "price": "5 USD/мес",
        "notes": "заметка",
        "os_users": [{"login": "root", "password": "p1", "role": "root", "label": ""}],
        "ssh_keys": [{"label": "основной", "key_type": "ed25519",
                      "public_key": "ssh-ed25519 AAAA", "private_key": "-----BEGIN",
                      "passphrase": "pp"}],
        "panels": [{"panel_type": "3x-ui", "url": "https://203.0.113.10",
                    "login": "admin", "password": "p2"}],
    }
    storage = d.to_storage()
    assert storage["payload"]["v"] == 1
    back = ServerData.from_storage(storage)
    assert back.name == "VPS-1"
    assert back.payload["host"] == "203.0.113.10"
    assert back.payload["price"] == "5 USD/мес"
    assert back.payload["extra_ips"] == ["10.0.0.5", "2a01:4f8::1"]
    assert back.payload["os_users"] == [
        {"login": "root", "password": "p1", "role": "root", "label": ""}]
    assert back.payload["ssh_keys"][0]["private_key"] == "-----BEGIN"
    assert back.payload["panels"][0] == {
        "panel_type": "3x-ui", "url": "https://203.0.113.10",
        "login": "admin", "password": "p2"}


def test_server_data_extra_ips_tolerant_legacy_string():
    """До перехода на список extra_ips хранился одной строкой с IP по
    строкам (УИ §2026-07-15) — старый формат читается толерантно."""
    d = ServerData.from_storage({
        "name": "Легаси", "payload": {"extra_ips": "10.0.0.5\n2a01:4f8::1\n"}})
    assert d.payload["extra_ips"] == ["10.0.0.5", "2a01:4f8::1"]


def test_server_data_tolerant_empty():
    d = ServerData.from_storage(None)
    assert d.name == "Новый сервер"
    assert d.gallery == []
    assert d.payload["os_users"] == []
    assert d.payload["ssh_keys"] == []
    assert d.payload["panels"] == []
    assert d.payload["extra_ips"] == []
    assert d.to_storage()["payload"]["v"] == 1


def test_server_data_tolerant_old_incomplete_payload():
    """Старый/неполный payload (без списков, без части скаляров, с мусорными
    элементами списка, с убранными из панелей port/label) не роняет чтение —
    недостающее становится пусто/дефолт, лишние ключи отбрасываются."""
    storage = {
        "name": "Легаси",
        "payload": {"host": "1.2.3.4",
                    "os_users": [{"login": "root"}, "мусор", 42],
                    "panels": [{"panel_type": "3x-ui", "url": "https://x",
                                "port": "2053", "login": "admin",
                                "password": "p", "label": "старая метка"}]},
        # gallery отсутствует вовсе
    }
    d = ServerData.from_storage(storage)
    assert d.name == "Легаси"
    assert d.payload["host"] == "1.2.3.4"
    # Толерантность: недостающие ключи элемента -> "", мусорные элементы отброшены.
    assert d.payload["os_users"] == [
        {"login": "root", "password": "", "role": "", "label": ""}]
    assert d.payload["ssh_keys"] == []
    # port/label убраны из панелей (УИ §2026-07-15) — старые ключи отброшены.
    assert d.payload["panels"] == [
        {"panel_type": "3x-ui", "url": "https://x", "login": "admin",
         "password": "p"}]
    assert d.payload["extra_ips"] == []
    assert d.gallery == []


@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("", None),
    ("   ", None),
    ("2026-08-01", "2026-08-01"),
    ("  до 15 августа  ", "до 15 августа"),
])
def test_extract_paid_until(value, expected):
    payload = {} if value is None else {"paid_until": value}
    assert extract_paid_until(payload) == expected


# ----- add / get / save -----

def test_add_and_get_server(db):
    sid = db.add_service("Провайдер")
    srv_id = db.add_server(sid, "VPS-1")
    storage = db.get_server(srv_id)
    assert storage is not None
    assert storage["name"] == "VPS-1"
    assert storage["service_id"] == sid
    assert storage["payload"] == {}
    assert storage["gallery"] == []
    assert storage["account_ids"] == []


def test_add_free_server(db):
    srv_id = db.add_server(None, "Свободный")
    storage = db.get_server(srv_id)
    assert storage["service_id"] is None


def test_get_missing_returns_none(db):
    assert db.get_server(999) is None


def test_save_payload_roundtrip_and_paid_until_extract(db):
    srv_id = db.add_server(None, "VPS")
    storage = db.get_server(srv_id)
    storage["name"] = "VPS Frankfurt"
    storage["payload"] = {
        "v": 1, "host": "203.0.113.10", "ssh_port": "22", "paid_until": "2026-08-01",
        "price": "10 USD/мес", "extra_ips": ["203.0.113.11"],
        "os_users": [{"login": "root", "password": "secret", "role": "root", "label": ""}],
        "ssh_keys": [{"label": "k1", "key_type": "ed25519", "public_key": "pub",
                      "private_key": "priv", "passphrase": "pp"}],
        "panels": [{"panel_type": "3x-ui", "url": "https://x",
                    "login": "admin", "password": "pw"}],
    }
    db.save_server(srv_id, storage)

    again = db.get_server(srv_id)
    assert again["name"] == "VPS Frankfurt"
    assert again["payload"]["host"] == "203.0.113.10"
    assert again["payload"]["price"] == "10 USD/мес"
    assert again["payload"]["extra_ips"] == ["203.0.113.11"]
    assert again["payload"]["os_users"][0]["password"] == "secret"
    assert again["payload"]["ssh_keys"][0]["private_key"] == "priv"
    assert again["payload"]["panels"][0]["login"] == "admin"
    assert "port" not in again["payload"]["panels"][0]

    db.cursor.execute("SELECT paid_until FROM servers WHERE id = ?", (srv_id,))
    assert db.cursor.fetchone()["paid_until"] == "2026-08-01"


def test_save_updates_paid_until_on_change(db):
    srv_id = db.add_server(None, "VPS")
    storage = db.get_server(srv_id)
    storage["payload"] = {"paid_until": "2026-08-01"}
    db.save_server(srv_id, storage)
    storage2 = db.get_server(srv_id)
    storage2["payload"] = {"paid_until": ""}
    db.save_server(srv_id, storage2)
    db.cursor.execute("SELECT paid_until FROM servers WHERE id = ?", (srv_id,))
    assert db.cursor.fetchone()["paid_until"] is None


def test_save_tolerant_to_old_payload_missing_lists(db):
    """Сохранение payload без списковых секций (старый формат) не падает: DAO
    хранит payload как есть (raw pass-through, по образцу fin_items), толерантное
    восполнение отсутствующих ключей ([]/дефолт) — на модели ServerData, а не в
    БД-слое (см. test_server_data_tolerant_old_incomplete_payload)."""
    srv_id = db.add_server(None, "VPS")
    storage = db.get_server(srv_id)
    storage["payload"] = {"host": "1.2.3.4"}   # без os_users/ssh_keys/panels
    db.save_server(srv_id, storage)
    again = db.get_server(srv_id)
    assert again["payload"]["host"] == "1.2.3.4"
    assert "os_users" not in again["payload"]
    # Модель ServerData поверх такого payload по-прежнему толерантна:
    from hranilka.data.models.server_data import ServerData
    model = ServerData.from_storage(again)
    assert model.payload["os_users"] == []
    assert model.payload["ssh_keys"] == []
    assert model.payload["panels"] == []


def test_save_missing_raises(db):
    with pytest.raises(ValueError):
        db.save_server(999, {"name": "x", "payload": {}, "gallery": []})


# ----- Связи сервер ↔ аккаунт -----

def test_server_links_both_sides(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    a2 = db.add_account(sid, "A2")
    srv = db.add_server(sid, "VPS")
    db.set_server_links(srv, [a1, a2])
    assert set(db.get_server_links(srv)) == {a1, a2}
    servers_of_a1 = db.get_account_server_links(a1)
    assert len(servers_of_a1) == 1
    assert servers_of_a1[0]["id"] == srv


def test_server_links_dedup_and_replace(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    a2 = db.add_account(sid, "A2")
    srv = db.add_server(sid, "VPS")
    db.set_server_links(srv, [a1, a1, a1])       # дубли схлопываются
    assert db.get_server_links(srv) == [a1]
    db.set_server_links(srv, [a2])               # перезапись
    assert db.get_server_links(srv) == [a2]


def test_server_link_accounts_excludes_deleted(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    srv = db.add_server(sid, "VPS")
    db.set_server_links(srv, [a1])
    assert len(db.get_server_link_accounts(srv)) == 1
    db.move_account_to_bin(a1)
    assert db.get_server_link_accounts(srv) == []


def test_list_servers_excludes_deleted(db):
    srv = db.add_server(None, "VPS")
    assert any(r["id"] == srv for r in db.list_servers())
    db.move_server_to_bin(srv)
    assert all(r["id"] != srv for r in db.list_servers())


def test_save_with_links_atomic(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    srv = db.add_server(sid, "VPS")
    storage = db.get_server(srv)
    storage["name"] = "N"
    db.save_server_with_links(srv, storage, [a1])
    assert db.get_server_links(srv) == [a1]
    assert db.get_server(srv)["name"] == "N"


def test_save_with_links_none_does_not_touch_links(db):
    """H-02: account_ids=None — save_server_with_links НЕ трогает server_links
    (отличие от []="снять все связи"). Карточка при этом сохраняется как
    обычно — None относится только к связям."""
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    srv = db.add_server(sid, "VPS")
    db.set_server_links(srv, [a1])
    storage = db.get_server(srv)
    storage["name"] = "N2"
    db.save_server_with_links(srv, storage, None)
    assert db.get_server_links(srv) == [a1]          # связь цела
    assert db.get_server(srv)["name"] == "N2"         # карточка сохранена


def test_save_with_links_empty_list_still_clears(db):
    """Контроль: [] (в отличие от None) по-прежнему снимает все связи —
    семантики не перепутаны."""
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    srv = db.add_server(sid, "VPS")
    db.set_server_links(srv, [a1])
    storage = db.get_server(srv)
    db.save_server_with_links(srv, storage, [])
    assert db.get_server_links(srv) == []


# ----- M-03: повреждённый JSON-payload -----

def test_get_server_corrupted_json_raises_and_row_untouched(db):
    """Невалидный JSON в servers.data не подменяется молча {} (это стёрло бы
    признак повреждения) — get_server поднимает CorruptedPayloadError, а
    строка в БД остаётся нетронутой самим фактом чтения."""
    from hranilka.data.errors import CorruptedPayloadError
    srv = db.add_server(None, "VPS")
    db.cursor.execute(
        "UPDATE servers SET data = ? WHERE id = ?", ("{not valid json", srv))
    db.conn.commit()

    with pytest.raises(CorruptedPayloadError):
        db.get_server(srv)

    db.cursor.execute("SELECT data FROM servers WHERE id = ?", (srv,))
    assert db.cursor.fetchone()["data"] == "{not valid json"


def test_link_cascade_on_account_delete(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    srv = db.add_server(sid, "VPS")
    db.set_server_links(srv, [a1])
    db.delete_account(a1)                        # FK CASCADE убирает связь
    assert db.get_server_links(srv) == []


def test_account_side_link_rows_helper(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    srv1 = db.add_server(sid, "VPS1")
    srv2 = db.add_server(sid, "VPS2")
    with db.conn:
        db._set_account_server_links_rows(a1, [srv1, srv2])
    assert set(db.get_server_links(srv1)) == {a1}
    assert set(db.get_server_links(srv2)) == {a1}


# ----- Корзина -----

def test_server_bin_move_restore(db):
    sid = db.add_service("S")
    srv = db.add_server(sid, "VPS")
    db.move_server_to_bin(srv)
    assert db.count_active_servers() == 0
    assert db.count_servers() == 1
    deleted = db.get_deleted_servers()
    assert deleted[0]["id"] == srv
    db.restore_server(srv)
    assert db.count_active_servers() == 1
    assert db.get_deleted_servers() == []


def test_server_delete_forever_cascades_gallery_and_links(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    srv = db.add_server(sid, "VPS")
    db.set_server_links(srv, [a1])
    storage = db.get_server(srv)
    storage["gallery"] = [{"desc": "скрин", "data": b"\x00" * 10}]
    db.save_server(srv, storage)

    db.delete_server_forever(srv)

    assert db.get_server(srv) is None
    db.cursor.execute("SELECT COUNT(*) AS n FROM server_links WHERE server_id = ?", (srv,))
    assert db.cursor.fetchone()["n"] == 0
    db.cursor.execute("SELECT COUNT(*) AS n FROM server_gallery WHERE server_id = ?", (srv,))
    assert db.cursor.fetchone()["n"] == 0


# ----- Избранное, порядок, перемещение -----

def test_server_favorite(db):
    sid = db.add_service("S")
    srv = db.add_server(sid, "VPS")
    db.set_server_favorite(srv, True)
    db.cursor.execute("SELECT is_favorite FROM servers WHERE id = ?", (srv,))
    assert db.cursor.fetchone()["is_favorite"] == 1
    db.set_server_favorite(srv, False)
    db.cursor.execute("SELECT is_favorite FROM servers WHERE id = ?", (srv,))
    assert db.cursor.fetchone()["is_favorite"] == 0


def test_server_favorites_batch(db):
    sid = db.add_service("S")
    s1 = db.add_server(sid, "VPS1")
    s2 = db.add_server(sid, "VPS2")
    db.set_server_favorites([s1, s2], True)
    db.cursor.execute("SELECT is_favorite FROM servers WHERE id IN (?, ?)", (s1, s2))
    assert all(r["is_favorite"] == 1 for r in db.cursor.fetchall())


def test_servers_order(db):
    sid = db.add_service("S")
    s1 = db.add_server(sid, "B")
    s2 = db.add_server(sid, "A")
    db.set_servers_order([s2, s1])
    db.cursor.execute("SELECT id FROM servers ORDER BY sort_order")
    assert [r["id"] for r in db.cursor.fetchall()] == [s2, s1]


def test_move_server(db):
    s1 = db.add_service("S1")
    s2 = db.add_service("S2")
    srv = db.add_server(s1, "VPS")
    db.move_server(srv, s2)
    db.cursor.execute("SELECT service_id FROM servers WHERE id = ?", (srv,))
    assert db.cursor.fetchone()["service_id"] == s2


def test_move_servers_batch(db):
    s1 = db.add_service("S1")
    s2 = db.add_service("S2")
    a = db.add_server(s1, "A")
    b = db.add_server(s1, "B")
    db.move_servers([a, b], s2)
    db.cursor.execute("SELECT service_id FROM servers WHERE id IN (?, ?)", (a, b))
    assert all(r["service_id"] == s2 for r in db.cursor.fetchall())


# ----- Счётчики -----

def test_counters(db):
    sid = db.add_service("S")
    s1 = db.add_server(sid, "A")
    s2 = db.add_server(sid, "B")
    db.move_server_to_bin(s2)
    assert db.count_servers() == 2
    assert db.count_active_servers() == 1


# ----- Галерея -----

def test_server_gallery_add_read_delete(db):
    srv = db.add_server(None, "VPS")
    storage = db.get_server(srv)
    storage["gallery"] = [{"desc": "панель", "data": b"\x01\x02\x03"}]
    ids = db.save_server(srv, storage)
    assert len(ids) == 1 and ids[0] is not None
    image_id = ids[0]

    assert db.load_server_gallery_image(image_id) == b"\x01\x02\x03"

    again = db.get_server(srv)
    assert again["gallery"][0]["desc"] == "панель"
    assert again["gallery"][0]["data"] is None            # ленивая загрузка
    assert again["gallery"][0]["blob_size"] == 3

    # Удаление: пустой список -> строка исчезает.
    again["gallery"] = []
    db.save_server(srv, again)
    assert db.get_server(srv)["gallery"] == []
    assert db.load_server_gallery_image(image_id) is None


def test_server_gallery_counted_in_shared_budget(db):
    sid = db.add_service("S")
    srv = db.add_server(sid, "VPS")

    assert db.gallery_total_bytes() == 0

    storage = db.get_server(srv)
    storage["gallery"] = [{"desc": "s", "data": b"x" * 5}]
    db.save_server(srv, storage)
    assert db.gallery_total_bytes() == 5
    assert db.gallery_total_bytes(exclude_server_id=srv) == 0


def test_server_gallery_enforces_common_limit(db, monkeypatch):
    """Серверы участвуют в том же общем лимите 500 МБ, что и остальные галереи."""
    import sqlite3

    from hranilka.data.database import gallery_ops

    monkeypatch.setattr(gallery_ops, "GALLERY_TOTAL_BYTES_LIMIT", 10)
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    db.cursor.execute(
        "INSERT INTO gallery (account_id, description, image_data) VALUES (?, ?, ?)",
        (aid, "акк", sqlite3.Binary(b"x" * 8)))
    db.conn.commit()
    db._invalidate_gallery_bytes()

    srv = db.add_server(sid, "VPS")
    storage = db.get_server(srv)
    storage["gallery"] = [{"desc": "srv", "data": b"y" * 3}]

    assert db.gallery_total_bytes() == 8
    with pytest.raises(ValueError, match="лимит"):
        db.save_server(srv, storage)
    assert db.gallery_total_bytes() == 8


# ----- wipe_all_data затрагивает и серверы -----

def test_wipe_all_data_removes_free_servers_and_gallery(db):
    srv = db.add_server(None, "Свободный сервер")
    storage = db.get_server(srv)
    storage["gallery"] = [{"desc": "чек", "data": b"blob"}]
    db.save_server(srv, storage)

    db.wipe_all_data()

    for table in ("servers", "server_links", "server_gallery"):
        db.cursor.execute(f"SELECT COUNT(*) AS n FROM {table}")
        assert db.cursor.fetchone()["n"] == 0, table
    assert db.get_server(srv) is None
