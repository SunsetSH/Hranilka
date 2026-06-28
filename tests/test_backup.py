"""Бэкапы: усиленная валидация (H3-02), roundtrip, откат, durability."""
import sqlite3
from pathlib import Path

import pytest

import backup


def _make_db(path):
    from database import Database
    d = Database(str(path))
    d.connect()
    d.create_tables()
    d.cursor.execute("INSERT INTO folders(name) VALUES('F1')")
    d.conn.commit()
    d.close(persist=False)


def test_is_valid_db_rejects_magic_only(tmp_path):
    f = tmp_path / "fake.db"
    f.write_bytes(b"HRNKv1")
    assert backup._is_valid_db(f) is False


def test_is_valid_db_rejects_foreign_sqlite(tmp_path):
    other = tmp_path / "other.db"
    con = sqlite3.connect(str(other))
    con.execute("CREATE TABLE x(a)")
    con.commit()
    con.close()
    assert backup._is_valid_db(other) is False


def test_is_valid_db_accepts_real(tmp_path):
    real = tmp_path / "real.db"
    _make_db(real)
    assert backup._is_valid_db(real) is True


def test_create_and_restore_roundtrip(tmp_path):
    dbp = tmp_path / "hranilka.db"
    _make_db(dbp)
    folder = tmp_path / "backups"
    dest = backup.create_backup(str(dbp), str(folder))
    assert dest.exists() and dest.stat().st_size > 0

    dbp.write_bytes(b"garbage not a db")          # «портим» рабочую БД
    backup.restore_backup(str(dest), str(dbp))
    con = sqlite3.connect(str(dbp))
    assert con.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 1
    con.close()


def test_restore_invalid_candidate_keeps_current(tmp_path):
    dbp = tmp_path / "hranilka.db"
    _make_db(dbp)
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"HRNKv1")
    with pytest.raises(ValueError):
        backup.restore_backup(str(bad), str(dbp))
    # текущая БД цела
    con = sqlite3.connect(str(dbp))
    assert con.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 1
    con.close()
    # без мусора-стейджа
    leftovers = [p.name for p in tmp_path.iterdir()
                 if p.name.endswith((".rollback", ".restore-tmp"))]
    assert leftovers == []
