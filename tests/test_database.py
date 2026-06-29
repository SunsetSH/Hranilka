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
