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


# ─── M-02: ротация/удаление бэкапов затирают plaintext через best_effort_wipe ─

def test_rotate_wipes_evicted_backup(tmp_path, monkeypatch):
    """Старейший ротируемый файл убирается через best_effort_wipe (не голым
    unlink) — при выключенном шифровании это полная plaintext-копия БД."""
    folder = tmp_path / "backups"
    folder.mkdir()
    names = ["hranilka_backup_20240101_000000_000001.db",
             "hranilka_backup_20240101_000000_000002.db"]
    for n in names:
        (folder / n).write_bytes(b"secret-plaintext-db")

    calls = []

    def spy_wipe(path):
        calls.append(path)
        Path(path).unlink(missing_ok=True)

    monkeypatch.setattr(backup, "best_effort_wipe", spy_wipe)
    backup._rotate(folder, keep_count=1)

    assert len(calls) == 1
    assert calls[0].endswith(names[0])                  # затёрт именно старейший
    remaining = sorted(p.name for p in folder.glob(backup._GLOB))
    assert remaining == names[1:]


def test_delete_all_backups_wipes_each_file(tmp_path, monkeypatch):
    """«Удалить все бэкапы» затирает каждый файл через best_effort_wipe."""
    folder = tmp_path / "backups"
    folder.mkdir()
    names = ["hranilka_backup_20240101_000000_000001.db",
             "hranilka_backup_20240101_000000_000002.db"]
    for n in names:
        (folder / n).write_bytes(b"secret-plaintext-db")

    calls = []

    def spy_wipe(path):
        calls.append(path)
        Path(path).unlink(missing_ok=True)

    monkeypatch.setattr(backup, "best_effort_wipe", spy_wipe)
    count = backup.delete_all_backups(str(folder))

    assert count == 2
    assert len(calls) == 2
    assert list(folder.glob(backup._GLOB)) == []


def test_create_backup_wipes_partial_tmp_on_copy_failure(tmp_path, monkeypatch):
    """Обрыв копирования в create_backup — недописанный tmp-файл (частичная
    plaintext-копия БД) затирается через best_effort_wipe, а не голым unlink."""
    dbp = tmp_path / "hranilka.db"
    _make_db(dbp)
    folder = tmp_path / "backups"

    calls = []

    def spy_wipe(path):
        calls.append(path)
        Path(path).unlink(missing_ok=True)

    def boom_copy(src, dst):
        Path(dst).write_bytes(b"partial-secret")
        raise OSError("disk full")

    monkeypatch.setattr(backup, "best_effort_wipe", spy_wipe)
    monkeypatch.setattr(backup, "_copy_durable", boom_copy)

    with pytest.raises(OSError):
        backup.create_backup(str(dbp), str(folder))

    assert len(calls) == 1
    assert [p.name for p in folder.iterdir() if p.name.endswith(".tmp")] == []


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


def test_validate_backup_public(tmp_path):
    """Публичная проверка кандидата: валидная БД Хранилки — True, мусор и
    отсутствующий файл — False (нужна вызывателям ДО перемещения текущего файла)."""
    from hranilka.data.database import Database
    from hranilka.services import backup as bk

    good = tmp_path / "good.db"
    d = Database(str(good))
    d.connect()
    d.create_tables()
    d.close()
    assert bk.validate_backup(str(good)) is True

    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not-a-sqlite-database")
    assert bk.validate_backup(str(bad)) is False
    assert bk.validate_backup(str(tmp_path / "missing.db")) is False
