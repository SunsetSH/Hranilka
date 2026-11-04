"""Регрессии пакета правок слоя данных: dirty-флаг, мьютекс записи, durable-копия,
транзакция миграций, CorruptedPayload, связи с корзиной/устаревшими id,
индексы после legacy-пересборки, wipe plaintext, OverflowError в config."""
import os
import threading

import pytest

from hranilka.data.database import CorruptedPayloadError, Database
from hranilka.data.errors import PreMigrationBackupError

_PW = "pw"
_PRESET = "fast"


def _enc(path):
    d = Database(path)
    d.connect()
    d.create_tables()
    d.enable_encryption(_PW, _PRESET)
    return d


def _close(d):
    d.close(persist=False)
    d.shutdown_executor()


# ----- 1: disable_encryption сохраняет dirty при сбое -----

def test_disable_encryption_failure_keeps_dirty(tmp_db_path, monkeypatch):
    d = _enc(tmp_db_path)
    d.add_service("S")
    assert d._dirty
    monkeypatch.setattr(d, "_atomic_write",
                        lambda data: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        d.disable_encryption()
    assert d.encrypted and d._dirty
    assert [s for s in d.conn.execute("SELECT name FROM services")][0][0] == "S"
    _close(d)


# ----- 2: flush не затирает пометку конкурентного мутатора -----

def test_flush_does_not_wipe_concurrent_dirty(tmp_db_path, monkeypatch):
    d = _enc(tmp_db_path)
    d.add_service("S")
    real = d.persist

    def persist_and_mutate(force=False):
        real(force=force)
        d._mark_dirty()          # мутатор успел во время записи

    monkeypatch.setattr(d, "persist", persist_and_mutate)
    d.flush()
    assert d._dirty
    _close(d)


def test_flush_failure_restores_dirty(tmp_db_path, monkeypatch):
    d = _enc(tmp_db_path)
    d.add_service("S")
    monkeypatch.setattr(d, "persist",
                        lambda force=False: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        d.flush()
    assert d._dirty
    _close(d)


# ----- 3: параллельные писатели не дают ложный конфликт -----

def test_concurrent_writers_no_spurious_conflict(tmp_db_path):
    d = _enc(tmp_db_path)
    d.add_service("S")
    errors = []

    def worker():
        try:
            for _ in range(5):
                d.persist()
        except Exception as e:      # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []
    _close(d)


def test_persist_uses_header_snapshot_inside_write_lock(tmp_db_path):
    d = _enc(tmp_db_path)
    d.change_master_password("new", _PRESET)
    d.persist()
    d.close()
    d.shutdown_executor()
    from hranilka.crypto import store as cs
    with open(tmp_db_path, "rb") as f:
        cs.unlock(f.read(), "new")


# ----- 4: _make_durable_copy не оставляет 0-байтный файл -----

def test_durable_copy_source_open_failure_leaves_no_file(db, tmp_db_path,
                                                         monkeypatch):
    import builtins
    real_open = builtins.open

    def fake_open(path, mode="r", *a, **kw):
        if str(path) == tmp_db_path and "r" in mode:
            raise PermissionError("locked")
        return real_open(path, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", fake_open)
    with pytest.raises(PreMigrationBackupError):
        db._make_durable_copy(1, "тест")
    monkeypatch.undo()
    leftovers = [n for n in os.listdir(os.path.dirname(tmp_db_path))
                 if ".pre-migrate" in n]
    assert leftovers == []


# ----- 5: транзакция миграций -----

def test_migration_failure_rolls_back_all_steps(tmp_db_path, monkeypatch):
    from hranilka.data.database import migrations
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    d.conn.execute("DROP TABLE server_gallery")
    d.conn.execute("DROP TABLE server_links")
    d.conn.execute("DROP TABLE servers")
    d.conn.execute("UPDATE app_meta SET value='11' WHERE key='schema_version'")
    d.conn.commit()

    def boom(conn):
        raise RuntimeError("step fail")

    real12 = migrations.MIGRATIONS[12]
    monkeypatch.setitem(migrations.MIGRATIONS, 12, real12)
    monkeypatch.setitem(migrations.MIGRATIONS, 13, boom)
    with pytest.raises(RuntimeError):
        d._migrate()
    names = {r[0] for r in d.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "servers" not in names            # m012 откатилась вместе с m013
    assert d.get_schema_version() == 11
    assert not d.conn.in_transaction
    _close(d)


def test_encrypted_failed_migration_not_flushed(tmp_db_path, monkeypatch):
    d = _enc(tmp_db_path)
    d.conn.execute("UPDATE app_meta SET value='11' WHERE key='schema_version'")
    d.conn.execute("DROP INDEX idx_accounts_deleted")
    d.conn.commit()
    d._dirty = False
    monkeypatch.setattr(d, "_migrate",
                        lambda: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        d.create_tables()
    assert d._dirty is False
    _close(d)


# ----- 6: CorruptedPayloadError для fin_items -----

def test_load_fin_item_corrupted_raises(db):
    sid = db.add_service("Банк")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.conn.execute("UPDATE fin_items SET data = '{broken' WHERE id = ?", (iid,))
    db.conn.commit()
    with pytest.raises(CorruptedPayloadError):
        db.load_fin_item(iid)
    # строка в БД не тронута
    assert db.conn.execute(
        "SELECT data FROM fin_items WHERE id=?", (iid,)).fetchone()[0] == "{broken"


def test_bulk_export_corrupted_logs_not_fails(db, caplog):
    sid = db.add_service("Банк")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.conn.execute("UPDATE fin_items SET data = 'zzz' WHERE id = ?", (iid,))
    db.conn.commit()
    with caplog.at_level("WARNING"):
        items = db._load_fin_items_bulk([iid], load_gallery=False)
    assert items[iid]["payload"] == {}
    assert any("повреждён" in r.message for r in caplog.records)
    assert "zzz" not in caplog.text


# ----- 7/8: связи -----

def _acc(db, name):
    sid = db.add_service("S" + name)
    return db.add_account(sid, name)


def test_set_links_keeps_bin_links_and_skips_stale(db):
    a, b, c = _acc(db, "a"), _acc(db, "b"), _acc(db, "c")
    db.set_links(a, [b, c])
    db.conn.execute(
        "UPDATE accounts SET deleted_at='2020-01-01 00:00:00' WHERE id=?", (c,))
    db.conn.commit()
    db.set_links(a, [b, 99999])            # 99999 - устаревший id, без IntegrityError
    pairs = {tuple(r) for r in db.conn.execute(
        "SELECT account_id, linked_account_id FROM linked_accounts")}
    assert (min(a, c), max(a, c)) in pairs          # связь с корзиной цела
    assert (min(a, b), max(a, b)) in pairs
    assert len(pairs) == 2


def test_fin_and_server_links_skip_stale_ids(db):
    a = _acc(db, "a")
    sid = db.add_service("Банк")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    srv = db.add_server(sid, "VPS")
    db.set_item_links(iid, [a, 99999])
    assert db.get_item_links(iid) == [a]
    db.set_server_links(srv, [a, 99999])
    assert db.get_server_links(srv) == [a]
    with db.conn:
        db._set_account_fin_links_rows(a, [iid, 99999])
        db._set_account_server_links_rows(a, [srv, 99999])
    assert db.get_item_links(iid) == [a]
    assert db.get_server_links(srv) == [a]


# ----- 10: wipe plaintext при включении шифрования -----

def test_enable_encryption_wipes_old_plaintext_inode(tmp_db_path, monkeypatch):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    d.add_service("SECRET-MARKER-123")
    wiped = []
    import hranilka.data.database.persistence as p
    real = p.best_effort_wipe

    def spy(path):
        with open(path, "rb") as f:
            wiped.append(b"SECRET-MARKER-123" in f.read())
        real(path)

    monkeypatch.setattr(p, "best_effort_wipe", spy)
    d.enable_encryption(_PW, _PRESET)
    assert True in wiped                       # старый inode затирался
    assert not [n for n in os.listdir(os.path.dirname(tmp_db_path))
                if "pre-update-wipe" in n]
    _close(d)


# ----- 11: индексы возвращаются после legacy-пересборки -----

def test_indexes_restored_after_accounts_rebuild(tmp_db_path):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    d.conn.execute("DROP INDEX idx_accounts_service")
    d.conn.execute("DROP INDEX idx_accounts_deleted")
    d.conn.commit()
    d.create_tables()            # отпечаток не сходится -> восстановление
    idx = {r[0] for r in d.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_accounts_service", "idx_accounts_deleted"} <= idx
    _close(d)


# ----- 9: config OverflowError -----

def test_config_infinity_does_not_raise(pure_config):
    c = pure_config._sanitize({"font_size": float("inf"),
                               "idle_lock_mins": 1e999})
    assert c["font_size"] == pure_config.config["font_size"]
    assert isinstance(c["idle_lock_mins"], int)


# ----- Ревью-фиксы: логирование устаревших связей, остатки pre-update-wipe -----

def test_stale_link_ids_are_logged(db, caplog):
    import logging
    a = _acc(db, "a")
    with caplog.at_level(logging.INFO):
        db.set_links(a, [99999])
    assert any("99999" in r.getMessage() for r in caplog.records)
    caplog.clear()
    sid = db.add_service("Банк")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    srv = db.add_server(sid, "VPS")
    with caplog.at_level(logging.INFO):
        db.set_item_links(iid, [a, 88888])
        db.set_server_links(srv, [a, 77777])
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "88888" in text and "77777" in text


def test_stale_update_hold_wiped_on_open(tmp_db_path):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    _close(d)
    hold = f"{tmp_db_path}.pre-update-wipe-424242"
    with open(hold, "wb") as f:
        f.write(b"PLAINTEXT-LEFTOVER")
    d2 = Database(tmp_db_path)
    d2.connect()
    assert not os.path.exists(hold)
    d2.close(persist=False)
    d2.shutdown_executor()


def test_stale_update_hold_linked_to_live_file_is_only_unlinked(tmp_db_path):
    """Сбой ДО замены: ссылка указывает на живой файл — затирать нельзя."""
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    d.add_service("KEEP")
    d.close(persist=False)
    d.shutdown_executor()
    hold = f"{tmp_db_path}.pre-update-wipe-424242"
    try:
        os.link(tmp_db_path, hold)
    except OSError:
        pytest.skip("жёсткие ссылки недоступны")
    d2 = Database(tmp_db_path)
    d2.connect()
    assert not os.path.exists(hold)
    assert [r[0] for r in d2.conn.execute("SELECT name FROM services")] == ["KEEP"]
    d2.close(persist=False)
    d2.shutdown_executor()


def test_live_hold_of_this_process_is_kept(tmp_db_path):
    d = _enc(tmp_db_path)
    hold = f"{tmp_db_path}.pre-update-wipe-{os.getpid()}"
    with open(hold, "wb") as f:
        f.write(b"x")
    d._wipe_hold_active = True
    d._wipe_stale_update_holds()
    assert os.path.exists(hold)
    d._wipe_hold_active = False
    d._wipe_stale_update_holds()
    assert not os.path.exists(hold)
    _close(d)
