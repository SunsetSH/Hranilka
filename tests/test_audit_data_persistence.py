"""Аудит 2026-11, персистентность: конфликт-детектор, S7 (wipe), PRAGMA,
pre-migrate в шифр. режиме, P7 (diff дочерних строк), лимиты галереи."""
import glob
import os

import pytest

from hranilka.crypto import store as cs
from hranilka.data.database import Database, VaultConflictError
from hranilka.data.database.gallery_ops import (
    GALLERY_MAX_IMAGE_BYTES, GALLERY_MAX_IMAGES_PER_OWNER)

_PW = "pw"
_PRESET = "fast"


def _enc(tmp_db_path):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    d.enable_encryption(_PW, _PRESET)
    return d


def test_touch_by_antivirus_is_not_conflict(tmp_db_path):
    d = _enc(tmp_db_path)
    d.add_service("S1")
    d.persist()
    st = os.stat(tmp_db_path)
    os.utime(tmp_db_path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    d.add_service("S2")
    d.persist()                       # не должно быть VaultConflictError
    d.persist()                       # ревизия обновлена - и повторно ок
    d.close(persist=False)
    d.shutdown_executor()


def test_real_external_change_still_conflicts(tmp_db_path):
    d = _enc(tmp_db_path)
    other, _ = cs.create_vault(b"x" * 100, "other", _PRESET)
    with open(tmp_db_path, "wb") as f:
        f.write(other)
    with pytest.raises(VaultConflictError):
        d.persist()
    d.close(persist=False)
    d.shutdown_executor()


def test_plaintext_mode_conflict_stays_strict(tmp_db_path):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    assert d._disk_fingerprint is None
    d.close(persist=False)
    d.shutdown_executor()


def test_plaintext_pragmas_explicit(db):
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert db.conn.execute("PRAGMA synchronous").fetchone()[0] == 2     # FULL
    assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_enable_encryption_wipes_plaintext_premigrate(tmp_db_path):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    plain_copy = tmp_db_path + ".pre-migrate-v1-20200101-000000"
    with open(plain_copy, "wb") as f:
        f.write(b"SQLite format 3\x00 secret")
    d.enable_encryption(_PW, _PRESET)
    assert not os.path.exists(plain_copy)
    d.close(persist=False)
    d.shutdown_executor()


def test_enable_encryption_keeps_encrypted_premigrate_copy(tmp_db_path):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    enc_copy = tmp_db_path + ".pre-migrate-v1-20200101-000000"
    container, _ = cs.create_vault(b"z" * 50, "o", _PRESET)
    with open(enc_copy, "wb") as f:
        f.write(container)
    d.enable_encryption(_PW, _PRESET)
    assert os.path.exists(enc_copy)           # шифртекст не трогаем
    d.close(persist=False)
    d.shutdown_executor()


def test_encrypted_premigrate_copy_kept_after_migration(tmp_db_path):
    """Перед первой записью после миграции контейнер копируется и остаётся."""
    d = _enc(tmp_db_path)
    d.persist()
    before = open(tmp_db_path, "rb").read()
    path = d._make_durable_copy(11, "test")
    assert path and os.path.exists(path)
    assert open(path, "rb").read() == before
    d._finish_premigration_backup(path)
    assert os.path.exists(path)               # шифртекст сохраняется для отката
    d.close(persist=False)
    d.shutdown_executor()


def test_plaintext_premigrate_still_wiped_on_finish(db, tmp_db_path):
    path = db._make_durable_copy(11, "test")
    assert path and os.path.exists(path)
    db._finish_premigration_backup(path)
    assert not glob.glob(tmp_db_path + ".pre-migrate*")


def _card(**over):
    base = {
        "fields": {"account_name": "A", "url": None, "login": "l", "password": "p",
                   "creation_date": None, "password_changed_date": None,
                   "password_change_interval_days": None, "notes": "", "ip": "",
                   "browser": "", "os": "", "extra_info": ""},
        "personal": {"mobile_phone": "1", "first_name": "F", "last_name": "L",
                     "middle_name": "M", "birth_date": "", "address": ""},
        "questions": [], "recovery": {"phrase": "ph", "device_id": "dv"},
        "codes": [], "gallery": [],
    }
    base.update(over)
    return base


def _make_account(db):
    sid = db.add_service("S")
    return db.add_account(sid, "A")


def test_save_account_keeps_single_row_ids(db):
    aid = _make_account(db)
    db.save_account(aid, _card())
    ids1 = (db.conn.execute("SELECT id FROM personal_data WHERE account_id=?", (aid,)).fetchone()[0],
            db.conn.execute("SELECT id FROM recovery_phrases WHERE account_id=?", (aid,)).fetchone()[0])
    card = _card()
    card["personal"]["first_name"] = "Changed"
    card["recovery"]["phrase"] = "new phrase"
    db.save_account(aid, card)
    ids2 = (db.conn.execute("SELECT id FROM personal_data WHERE account_id=?", (aid,)).fetchone()[0],
            db.conn.execute("SELECT id FROM recovery_phrases WHERE account_id=?", (aid,)).fetchone()[0])
    assert ids1 == ids2
    loaded = db.load_account(aid)
    assert loaded["personal"]["first_name"] == "Changed"
    assert loaded["recovery"]["phrase"] == "new phrase"
    assert db.conn.execute("SELECT COUNT(*) FROM personal_data WHERE account_id=?", (aid,)).fetchone()[0] == 1


def test_save_account_still_rolls_back_missing_account(db):
    with pytest.raises(ValueError):
        db.save_account(99999, _card())


def test_gallery_rejects_oversized_image(db):
    aid = _make_account(db)
    big = b"\x00" * (GALLERY_MAX_IMAGE_BYTES + 1)
    with pytest.raises(ValueError):
        db.save_account(aid, _card(gallery=[{"desc": "", "data": big}]))
    assert db.conn.execute("SELECT COUNT(*) FROM gallery").fetchone()[0] == 0


def test_gallery_rejects_too_many_images(db):
    aid = _make_account(db)
    items = [{"desc": str(i), "data": b"x"} for i in range(GALLERY_MAX_IMAGES_PER_OWNER + 1)]
    with pytest.raises(ValueError):
        db.save_account(aid, _card(gallery=items))
    assert db.conn.execute("SELECT COUNT(*) FROM gallery").fetchone()[0] == 0
    ok = items[:GALLERY_MAX_IMAGES_PER_OWNER]
    db.save_account(aid, _card(gallery=ok))
    assert db.conn.execute("SELECT COUNT(*) FROM gallery").fetchone()[0] == GALLERY_MAX_IMAGES_PER_OWNER


def test_gallery_existing_rows_kept_when_saving_with_data_none(db):
    aid = _make_account(db)
    ids = db.save_account(aid, _card(gallery=[{"desc": "a", "data": b"x"}]))
    # повторное сохранение «оставить BLOB» - лимиты не мешают
    db.save_account(aid, _card(gallery=[{"desc": "b", "data": None, "image_id": ids[0]}]))
    assert db.conn.execute("SELECT COUNT(*) FROM gallery").fetchone()[0] == 1
