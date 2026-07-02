"""Тесты исправлений повторного ревью 0.6.5.

Покрывают логику без GUI:
  * H65-02 — токен сессии привязывает многошаговую async-операцию к одной сессии;
  * M65-04 — отпечаток схемы на быстром пути + foreign_key_check в валидации бэкапа;
  * M65-05 — pre-migration копия создаётся перед миграцией и снимается при успехе;
  * M65-03 — при неудачном откате rollback-копия сохраняется, а не удаляется;
  * L65-02 — ограниченное чтение файла.
"""
import os
import sqlite3
from pathlib import Path

import pytest

from database import Database, StaleSessionError, SCHEMA_VERSION


@pytest.fixture
def adb(tmp_db_path):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    yield d
    d.shutdown_executor()
    try:
        d.close(persist=False)
    except Exception:
        pass


# ─── H65-02: session token pins the whole coroutine ──────────────────────────

async def test_run_async_pinned_token_aborts_after_session_change(adb):
    """Токен, снятый в начале логической операции, делает StaleSessionError даже
    для вызова, поставленного УЖЕ после смены сессии (в отличие от снятия токена
    на каждый вызов) — так многошаговая операция целиком привязана к одной сессии."""
    token = adb.current_session()
    adb._bump_session()                         # lock/restore между await
    with pytest.raises(StaleSessionError):
        await adb.run_async(adb.get_all_accounts, _session=token)


async def test_run_async_pinned_token_ok_within_session(adb):
    token = adb.current_session()
    adb.add_service("S")
    rows = await adb.run_async(adb.get_all_accounts, _session=token)
    assert rows == []                           # сессия та же — операция выполнилась


# ─── M65-04: fast-path fingerprint ───────────────────────────────────────────

def test_fast_path_fingerprint_detects_missing_unique_index(db):
    """Удалённый вручную UNIQUE-индекс целостности ломает отпечаток быстрого пути,
    и create_tables его восстанавливает (раньше проверялись только имена таблиц)."""
    db.cursor.execute("DROP INDEX uq_personal_account")
    db._commit()
    assert db._fast_path_fingerprint_ok() is False

    db.create_tables()                          # проходит путь восстановления
    idx = {r["name"] for r in db.cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "uq_personal_account" in idx
    assert db._fast_path_fingerprint_ok() is True


# ─── M65-05: pre-migration backup ────────────────────────────────────────────

def test_premigration_backup_removed_on_success(db, tmp_db_path):
    """Перед миграцией версии создаётся *.pre-migrate, при успехе — удаляется."""
    db.set_schema_version(SCHEMA_VERSION - 1)
    db._commit()
    db.create_tables()                          # мигрирует до актуальной версии
    assert db.get_schema_version() == SCHEMA_VERSION
    assert not os.path.exists(tmp_db_path + ".pre-migrate")


def test_premigration_backup_kept_on_failure(db, tmp_db_path, monkeypatch):
    """Если миграция прервётся, pre-migration копия остаётся на диске для восстановления."""
    db.set_schema_version(SCHEMA_VERSION - 1)
    db._commit()

    def boom():
        raise RuntimeError("migration crashed")

    monkeypatch.setattr(db, "_migrate", boom)
    with pytest.raises(RuntimeError):
        db.create_tables()
    assert os.path.exists(tmp_db_path + ".pre-migrate")


def test_no_premigration_backup_when_schema_current(db, tmp_db_path):
    """Актуальная версия (только ремонт отпечатка) не плодит pre-migration копий."""
    db.cursor.execute("DROP INDEX uq_personal_account")   # версия та же, отпечаток бит
    db._commit()
    db.create_tables()
    assert not os.path.exists(tmp_db_path + ".pre-migrate")


# ─── M65-04: backup validation (foreign_key_check) ───────────────────────────

def test_is_valid_db_rejects_foreign_key_violation(tmp_path):
    """Бэкап с битой ссылочной целостностью не проходит проверку (M65-04)."""
    from backup import _is_valid_db
    p = tmp_path / "candidate.db"
    con = sqlite3.connect(p)
    con.executescript(
        "CREATE TABLE folders(id INTEGER PRIMARY KEY);"
        "CREATE TABLE services(id INTEGER PRIMARY KEY);"
        "CREATE TABLE accounts(id INTEGER PRIMARY KEY);"
        "CREATE TABLE gallery(id INTEGER PRIMARY KEY, "
        " account_id INTEGER REFERENCES accounts(id));"
        "INSERT INTO gallery(account_id) VALUES (999);"   # висячая ссылка
    )
    con.commit()
    con.close()
    assert _is_valid_db(p) is False


# ─── M65-03: failed rollback is preserved, not deleted ───────────────────────

def test_rollback_or_preserve_keeps_copy_when_swap_fails(tmp_path, monkeypatch):
    """Если сам откат (os.replace) не удался, последняя целая копия прежней БД не
    уничтожается — сохраняется под recovery-именем или остаётся rollback (M65-03)."""
    import backup
    dst = tmp_path / "hranilka.db"
    rollback = tmp_path / "hranilka.db.rollback"
    dst.write_bytes(b"broken-candidate")
    rollback.write_bytes(b"the-only-good-db")

    def failing_replace(src, dest):
        raise OSError("file locked")

    monkeypatch.setattr(backup.os, "replace", failing_replace)
    with pytest.raises(RuntimeError) as ei:
        backup._rollback_or_preserve(rollback, dst, had_dst=True)

    # rollback НЕ удалён (переименование в recovery тоже упало → остался на месте).
    assert rollback.exists()
    assert "сохранена" in str(ei.value)


# ─── L65-02: bounded file read ───────────────────────────────────────────────

def test_read_file_within_limit(tmp_path):
    from widgets import _read_file
    p = tmp_path / "f.bin"
    p.write_bytes(b"x" * 100)
    assert _read_file(str(p), 100) == b"x" * 100


def test_read_file_rejects_oversize(tmp_path):
    from widgets import _read_file, FileTooLargeError
    p = tmp_path / "f.bin"
    p.write_bytes(b"x" * 100)
    with pytest.raises(FileTooLargeError):
        _read_file(str(p), 99)
