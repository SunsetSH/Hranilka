"""Аудит 2026-11, бэкап: B6 (валидация папки), B7 (проверка до ротации,
sqlite3.backup, flush)."""
import os
import sqlite3

import pytest

from hranilka.crypto import store as cs
from hranilka.services import backup as bk


def test_empty_and_relative_folder_rejected(db, tmp_db_path):
    for bad in ("", "   ", "relative/dir", "."):
        with pytest.raises(ValueError):
            bk.create_backup(tmp_db_path, bad)
    assert bk.list_backups("") == []
    assert bk.list_backups("   ") == []


def test_plain_backup_is_consistent_snapshot_of_open_db(db, tmp_db_path, tmp_path):
    sid = db.add_service("Svc")
    db.add_account(sid, "Acc")
    out = bk.create_backup(tmp_db_path, str(tmp_path / "bk"))
    con = sqlite3.connect(out)
    try:
        assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
    finally:
        con.close()
    assert bk.validate_backup(str(out))
    # В папке бэкапов нет временных файлов.
    assert [n for n in os.listdir(tmp_path / "bk") if n.endswith(".tmp")] == []


def test_encrypted_container_backup_copied_raw(tmp_path):
    p = tmp_path / "v.db"
    container, _ = cs.create_vault(b"data" * 20, "pw", "fast")
    p.write_bytes(container)
    out = bk.create_backup(str(p), str(tmp_path / "bk"))
    assert out.read_bytes() == container


def test_flush_called_before_copy(db, tmp_db_path, tmp_path):
    calls = []
    bk.create_backup(tmp_db_path, str(tmp_path / "bk"),
                     flush=lambda: calls.append(1))
    assert calls == [1]


def test_flush_error_aborts_backup(db, tmp_db_path, tmp_path):
    def boom():
        raise OSError("flush failed")

    with pytest.raises(OSError):
        bk.create_backup(tmp_db_path, str(tmp_path / "bk"), flush=boom)
    assert not (tmp_path / "bk").exists() or bk.list_backups(str(tmp_path / "bk")) == []


def test_invalid_copy_does_not_rotate_old_backups(db, tmp_db_path, tmp_path, monkeypatch):
    folder = str(tmp_path / "bk")
    first = bk.create_backup(tmp_db_path, folder, keep_count=1)
    second_name_count = len(bk.list_backups(folder))
    monkeypatch.setattr(bk, "_is_valid_db", lambda p: False)
    with pytest.raises(ValueError):
        bk.create_backup(tmp_db_path, folder, keep_count=1)
    # Старый бэкап цел, неудачная копия не осталась (ни tmp, ни финал).
    assert first.exists()
    assert len(bk.list_backups(folder)) == second_name_count
    assert [n for n in os.listdir(folder) if n.endswith(".tmp")] == []


def test_rotation_keeps_newest(db, tmp_db_path, tmp_path):
    folder = str(tmp_path / "bk")
    made = [bk.create_backup(tmp_db_path, folder, keep_count=2) for _ in range(4)]
    left = bk.list_backups(folder)
    assert len(left) == 2
    assert set(left) == set(made[-2:])


def test_keep_count_zero_means_unlimited(db, tmp_db_path, tmp_path):
    folder = str(tmp_path / "bk")
    for _ in range(4):
        bk.create_backup(tmp_db_path, folder, keep_count=0)
    assert len(bk.list_backups(folder)) == 4


def test_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        bk.create_backup(str(tmp_path / "nope.db"), str(tmp_path / "bk"))
