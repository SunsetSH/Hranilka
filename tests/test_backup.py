"""Бэкапы: усиленная валидация (H3-02), roundtrip, откат, durability."""
import sqlite3
from pathlib import Path

import pytest

from hranilka.services import backup


def _make_db(path):
    from hranilka.data.database import Database
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


# ─── L-15: ротация и валидация зашифрованного контейнера ─────────────────────

def test_rotate_keeps_only_newest(tmp_path):
    """_rotate оставляет ровно keep_count самых новых бэкапов; старейший удаляется."""
    folder = tmp_path / "backups"
    folder.mkdir()
    # Имена сортируются по временной метке (лексикографически = хронологически).
    names = ["hranilka_backup_20240101_000000_000001.db",
             "hranilka_backup_20240101_000000_000002.db",
             "hranilka_backup_20240101_000000_000003.db"]
    for n in names:
        (folder / n).write_bytes(b"x")

    backup._rotate(folder, keep_count=2)
    remaining = sorted(p.name for p in folder.glob(backup._GLOB))
    assert remaining == names[1:]           # старейший (…_000001) удалён


def test_rotate_keep_count_zero_is_noop(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    (folder / "hranilka_backup_20240101_000000_000001.db").write_bytes(b"x")
    backup._rotate(folder, keep_count=0)    # 0 — не ротировать (не «удалить всё»)
    assert len(list(folder.glob(backup._GLOB))) == 1


def test_is_valid_db_accepts_encrypted_container(tmp_path):
    """Валидный зашифрованный контейнер (из crypto_store.create_vault на настоящих
    байтах БД) проходит валидацию бэкапа — структурный разбор заголовка успешен."""
    from hranilka.crypto import store as crypto_store

    real = tmp_path / "real.db"
    _make_db(real)
    db_bytes = real.read_bytes()            # валидный сериализованный SQLite-файл
    container, _ = crypto_store.create_vault(db_bytes, "master-pw", "fast")

    enc = tmp_path / "enc.hdb"
    enc.write_bytes(container)
    assert backup._is_valid_db(enc) is True

    # Обрезанный контейнер (битый заголовок) — отвергается.
    truncated = tmp_path / "trunc.hdb"
    truncated.write_bytes(container[:20])
    assert backup._is_valid_db(truncated) is False
