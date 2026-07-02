"""Схема/миграции, быстрый путь, пути аккаунтов, связи."""
import os
import sqlite3

import pytest

from database import Database, SCHEMA_VERSION, FutureSchemaError


def test_fresh_db_has_current_schema(db):
    assert db.get_schema_version() == SCHEMA_VERSION


def test_reopen_current_schema_fast_path(tmp_db_path):
    d = Database(tmp_db_path); d.connect(); d.create_tables(); d.close(persist=False)
    # повторное открытие актуальной схемы не должно падать (ранний выход)
    d2 = Database(tmp_db_path); d2.connect(); d2.create_tables()
    assert d2.get_schema_version() == SCHEMA_VERSION
    d2.close(persist=False)


def test_future_schema_refused(tmp_db_path):
    d = Database(tmp_db_path); d.connect(); d.create_tables()
    d.set_schema_version(SCHEMA_VERSION + 1)
    d._commit()
    d.close(persist=False)
    d2 = Database(tmp_db_path); d2.connect()
    with pytest.raises(FutureSchemaError):
        d2.create_tables()
    d2.close(persist=False)


def test_get_all_accounts_paths(db):
    fid = db.add_folder("Папка")
    sid = db.add_service("Сервис", fid)
    db.add_account(sid, "Акк1")
    rows = db.get_all_accounts()
    assert len(rows) == 1
    assert rows[0]["name"] == "Папка / Сервис / Акк1"


def test_links_symmetric(db):
    sid = db.add_service("S")
    a = db.add_account(sid, "A")
    b = db.add_account(sid, "B")
    db.set_links(a, [b])
    assert [r["id"] for r in db.get_links(a)] == [b]
    assert [r["id"] for r in db.get_links(b)] == [a]   # двусторонняя видимость


def test_links_dedup_and_no_self(db):
    sid = db.add_service("S")
    a = db.add_account(sid, "A")
    b = db.add_account(sid, "B")
    db.set_links(a, [b, b, a])         # повтор + самоссылка
    assert [r["id"] for r in db.get_links(a)] == [b]


def test_links_canonical_pair_in_schema(db):
    """После set_links(a,[b]) и set_links(b,[a]) в linked_accounts ровно одна
    строка, и она каноничная (account_id < linked_account_id)."""
    sid = db.add_service("S")
    a = db.add_account(sid, "A")
    b = db.add_account(sid, "B")
    db.set_links(a, [b])
    db.set_links(b, [a])
    db.cursor.execute("SELECT account_id, linked_account_id FROM linked_accounts")
    rows = db.cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["account_id"] < rows[0]["linked_account_id"]


def test_secure_delete_enabled(db):
    """PRAGMA secure_delete включён — удалённые данные затираются (Баг 2)."""
    row = db.conn.execute("PRAGMA secure_delete").fetchone()
    assert int(row[0]) == 1


def test_vacuum_shrinks_file_after_delete(tmp_path):
    """VACUUM физически уменьшает файл после удаления крупных BLOB (Баг 2)."""
    p = str(tmp_path / "v.db")
    d = Database(p)
    d.connect()
    d.create_tables()
    sid = d.add_service("S")
    aid = d.add_account(sid, "A")
    blob = sqlite3.Binary(b"\x00" * (2 * 1024 * 1024))
    for _ in range(5):
        d.cursor.execute(
            "INSERT INTO gallery (account_id, description, image_data) VALUES (?, ?, ?)",
            (aid, "x", blob))
    d._commit()
    size_full = os.path.getsize(p)

    d.cursor.execute("DELETE FROM gallery")
    d._commit()
    size_after_delete = os.path.getsize(p)
    assert size_after_delete >= size_full - 65536   # файл сам не сжался (freelist)

    d.vacuum()
    size_after_vacuum = os.path.getsize(p)
    assert size_after_vacuum < size_full            # VACUUM реально уменьшил файл
    d.close(persist=False)


def test_links_check_rejects_noncanonical(db):
    """Схема не даёт вставить обратную пару (B,A) или самоссылку (A,A)."""
    import sqlite3
    sid = db.add_service("S")
    a = db.add_account(sid, "A")
    b = db.add_account(sid, "B")
    lo, hi = (a, b) if a < b else (b, a)
    with pytest.raises(sqlite3.IntegrityError):
        db.cursor.execute(
            "INSERT INTO linked_accounts (account_id, linked_account_id) VALUES (?, ?)",
            (hi, lo))
    with pytest.raises(sqlite3.IntegrityError):
        db.cursor.execute(
            "INSERT INTO linked_accounts (account_id, linked_account_id) VALUES (?, ?)",
            (a, a))


def _card(gallery=None):
    """Минимальная карточка в формате save_account/_save_account_rows."""
    return {
        "fields": {
            "account_name": "A", "url": None, "login": None, "password": None,
            "creation_date": None, "password_changed_date": None,
            "password_change_interval_days": None, "notes": None, "ip": None,
            "browser": None, "os": None, "extra_info": None,
        },
        "personal": {"first_name": None, "last_name": None, "middle_name": None,
                     "birth_date": None, "address": None},
        "questions": [],
        "recovery": {"phrase": "", "device_id": ""},
        "codes": [],
        "gallery": gallery or [],
    }


def _gallery_rows(db, account_id):
    db.cursor.execute(
        "SELECT id, description, image_data FROM gallery "
        "WHERE account_id = ? ORDER BY id", (account_id,))
    return db.cursor.fetchall()


# ─── H-5: инвариант лока ──────────────────────────────────────────────────────

def test_serialize_db_stays_lock_wrapped():
    """serialize_db не должен попасть в _DB_NO_LOCK (иначе сериализация БД шла бы
    без лока параллельно записи в conn — гонка)."""
    import database
    assert "serialize_db" not in database._DB_NO_LOCK


# ─── H-6: контракт сохранения галереи ─────────────────────────────────────────

def test_save_gallery_lazy_item_preserves_blob(db):
    """data=None + image_id=X сохраняет существующий BLOB строки X (не удаляет,
    не обнуляет), обновляя лишь подпись."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    db.save_account(aid, _card([{"desc": "one", "data": b"AAA"}]))
    row = _gallery_rows(db, aid)[0]
    img_id = row["id"]
    assert bytes(row["image_data"]) == b"AAA"

    # Повторное сохранение с ленивым item: data=None, image_id указывает на строку.
    db.save_account(aid, _card([{"desc": "renamed", "data": None, "image_id": img_id}]))
    rows = _gallery_rows(db, aid)
    assert len(rows) == 1
    assert rows[0]["id"] == img_id
    assert bytes(rows[0]["image_data"]) == b"AAA"    # BLOB не тронут
    assert rows[0]["description"] == "renamed"       # подпись обновилась


def test_save_gallery_omitted_id_deletes(db):
    """Строка, чей image_id отсутствует в переданном списке, удаляется."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    db.save_account(aid, _card([{"desc": "keep", "data": b"AAA"},
                                {"desc": "drop", "data": b"BBB"}]))
    rows = _gallery_rows(db, aid)
    keep_id = rows[0]["id"]
    # Сохраняем только первую (ленивым item); вторую не передаём вовсе.
    db.save_account(aid, _card([{"desc": "keep", "data": None, "image_id": keep_id}]))
    rows = _gallery_rows(db, aid)
    assert len(rows) == 1
    assert rows[0]["id"] == keep_id
    assert bytes(rows[0]["image_data"]) == b"AAA"


def test_save_gallery_mixed_order_preserved(db):
    """Смешанный список (сохранённые + новые) сохраняет порядок: сохранённые по
    своим id, новые — в конце в порядке списка."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    db.save_account(aid, _card([{"desc": "g1", "data": b"111"},
                                {"desc": "g2", "data": b"222"}]))
    rows = _gallery_rows(db, aid)
    id1, id2 = rows[0]["id"], rows[1]["id"]
    # g1 остаётся (lazy), g2 остаётся (lazy), плюс новая g3 с байтами в конце.
    db.save_account(aid, _card([
        {"desc": "g1", "data": None, "image_id": id1},
        {"desc": "g2", "data": None, "image_id": id2},
        {"desc": "g3", "data": b"333"},
    ]))
    rows = _gallery_rows(db, aid)
    assert [r["description"] for r in rows] == ["g1", "g2", "g3"]
    assert [bytes(r["image_data"]) for r in rows] == [b"111", b"222", b"333"]
    assert rows[0]["id"] == id1 and rows[1]["id"] == id2


def test_save_gallery_returns_row_ids(db):
    """save_account возвращает id строк галереи в порядке items: существующие —
    свои, новые — lastrowid. UI по ним присваивает id новым картинкам, чтобы
    повторное сохранение не пересоздавало строку."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    ids = db.save_account(aid, _card([{"desc": "a", "data": b"AAA"},
                                      {"desc": "b", "data": b"BBB"}]))
    rows = _gallery_rows(db, aid)
    assert ids == [rows[0]["id"], rows[1]["id"]]

    # Смешанный список: lazy (свой id), новая (новый id), «мёртвый» id (None).
    ids2 = db.save_account(aid, _card([
        {"desc": "a", "data": None, "image_id": ids[0]},
        {"desc": "c", "data": b"CCC"},
        {"desc": "ghost", "data": None, "image_id": 999999},
    ]))
    rows = _gallery_rows(db, aid)
    assert ids2[0] == ids[0]
    assert ids2[1] == rows[-1]["id"]                 # новая строка в конце
    assert ids2[2] is None                           # чужой/мёртвый id пропущен

    # Повторное сохранение с возвращёнными id не пересоздаёт строки.
    ids3 = db.save_account(aid, _card([
        {"desc": "a", "data": None, "image_id": ids2[0]},
        {"desc": "c", "data": None, "image_id": ids2[1]},
    ]))
    assert ids3 == ids2[:2]


def test_save_gallery_bytes_path_unchanged(db):
    """Прежнее поведение (все items с bytes, без image_id) не изменилось:
    строки перезаписываются, порядок = порядок списка."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    db.save_account(aid, _card([{"desc": "x", "data": b"AAA"},
                                {"desc": "y", "data": b"BBB"}]))
    db.save_account(aid, _card([{"desc": "z", "data": b"CCC"}]))
    rows = _gallery_rows(db, aid)
    assert len(rows) == 1
    assert bytes(rows[0]["image_data"]) == b"CCC"


# ─── L-6: save_account с несуществующим аккаунтом откатывается ─────────────────

def test_save_account_missing_raises_and_rolls_back(db):
    import pytest as _pytest
    with _pytest.raises(ValueError):
        db.save_account(99999, _card())
    # Транзакция откатилась — мусорных связанных строк не осталось.
    db.cursor.execute("SELECT COUNT(*) AS n FROM personal_data WHERE account_id = 99999")
    assert db.cursor.fetchone()["n"] == 0


# ─── M-9: кэш суммарного объёма галереи ───────────────────────────────────────

def test_gallery_total_bytes_cache_and_invalidation(db):
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    assert db.gallery_total_bytes() == 0
    db.save_account(aid, _card([{"desc": "x", "data": b"\x00" * 100}]))
    assert db.gallery_total_bytes() == 100
    # exclude вычитает объём аккаунта из кэша.
    assert db.gallery_total_bytes(exclude_account_id=aid) == 0
    db.delete_account(aid)
    assert db.gallery_total_bytes() == 0


def test_links_migration_normalizes_old_rows(tmp_db_path):
    """Старая (v6) база с обратными дублями и самоссылкой нормализуется при
    открытии: остаётся одна каноничная строка, версия схемы поднимается до 7."""
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    sid = d.add_service("S")
    a = d.add_account(sid, "A")
    b = d.add_account(sid, "B")

    # Сымитировать СТАРУЮ таблицу связей: без CHECK, с дублями (A,B)/(B,A) и (A,A).
    d._commit()
    d.conn.execute("PRAGMA foreign_keys = OFF")
    d.cursor.execute("DROP TABLE linked_accounts")
    d.cursor.execute(
        "CREATE TABLE linked_accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "account_id INTEGER NOT NULL, linked_account_id INTEGER NOT NULL)")
    for pair in ((a, b), (b, a), (a, a)):
        d.cursor.execute(
            "INSERT INTO linked_accounts (account_id, linked_account_id) VALUES (?, ?)",
            pair)
    d.set_schema_version(6)
    d._commit()
    d.conn.execute("PRAGMA foreign_keys = ON")
    d.close(persist=False)

    # Повторное открытие запускает миграцию канонизации.
    d2 = Database(tmp_db_path)
    d2.connect()
    d2.create_tables()
    d2.cursor.execute("SELECT account_id, linked_account_id FROM linked_accounts")
    rows = d2.cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["account_id"] < rows[0]["linked_account_id"]
    assert d2.get_schema_version() == SCHEMA_VERSION
    d2.close(persist=False)
