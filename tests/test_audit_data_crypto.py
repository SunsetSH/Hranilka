"""Аудит 2026-11, крипто-слой: S1 (AAD), S2 (ротация DEK), S3 (NFC),
S8 (занулённый DEK), S9 (HKDF recovery), B13 (seal после lock), P1."""
import os
import struct
import threading
import unicodedata

import pytest

from hranilka.crypto import store as cs
from hranilka.data.database import Database
from hranilka.data.errors import StaleSessionError

_PRESET = "fast"
_DATA = b"SQLite format 3\x00" + b"x" * 200


def _make_v1(db_bytes, password, recovery="AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG-HHHH"):
    """Контейнер старого формата v1: без flags и AAD, пароль в сыром UTF-8,
    recovery через Argon2."""
    t, m, p = cs.PRESETS[_PRESET]
    dek = os.urandom(cs.DEK_LEN)
    salt_pw, salt_rec = os.urandom(16), os.urandom(16)
    wrap_pw = cs._wrap(dek, password, salt_pw, (t, m, p), nfc=False)
    wrap_rec = cs._wrap(dek, cs.normalize_recovery_code(recovery), salt_rec,
                        (t, m, p), nfc=False)
    nonce = os.urandom(12)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ct = AESGCM(dek).encrypt(nonce, db_bytes, None)
    blob = (cs.MAGIC + struct.pack("<BBBIB", 1, 0, t, m, p) + salt_pw
            + struct.pack("<H", len(wrap_pw)) + wrap_pw + salt_rec
            + struct.pack("<H", len(wrap_rec)) + wrap_rec + nonce + ct)
    return blob, recovery


def test_new_container_is_v2_with_flags():
    container, _ = cs.create_vault(_DATA, "pw", _PRESET)
    header, _, _ = cs._parse(container)
    assert header["version"] == 2
    assert header["flags"] == cs.FLAG_PW_NFC | cs.FLAG_REC_HKDF


def test_v1_container_still_unlocks_and_upgrades_on_seal():
    v1, rec = _make_v1(_DATA, "legacy-pw")
    plain, dek, header = cs.unlock(v1, "legacy-pw")
    assert plain == _DATA and header["version"] == 1 and header["flags"] == 0
    # Recovery (Argon2) старого формата тоже работает.
    assert cs.unlock(v1, rec, is_recovery=True)[0] == _DATA
    # Сохранение переписывает в v2; прежние кодировки (flags=0) сохраняются.
    v2 = cs.seal(_DATA + b"!", dek, header)
    h2, _, _ = cs._parse(v2)
    assert h2["version"] == 2 and h2["flags"] == 0
    assert cs.unlock(v2, "legacy-pw")[0] == _DATA + b"!"
    assert cs.unlock(v2, rec, is_recovery=True)[0] == _DATA + b"!"


@pytest.mark.parametrize("offset_name", ["preset", "salt_pw", "wrap_rec", "flags"])
def test_header_tamper_detected_by_aad(offset_name):
    container, _ = cs.create_vault(_DATA, "pw", _PRESET)
    base = len(cs.MAGIC) + 1                      # после VER
    offsets = {
        "flags": base,
        "preset": base + 1,
        "salt_pw": base + 1 + 1 + 1 + 4 + 1,
        "wrap_rec": base + 1 + 1 + 1 + 4 + 1 + 16 + 2 + 60 + 16 + 2 + 5,
    }
    off = offsets[offset_name]
    tampered = bytearray(container)
    tampered[off] ^= 0x01
    # preset-байт не участвует в KDF (параметры читаются из t/m/p) -> пароль
    # проходит, а тег данных обязан не сойтись; остальное падает раньше.
    with pytest.raises((cs.CorruptVault, cs.WrongPassword)):
        cs.unlock(bytes(tampered), "pw")


def test_data_tamper_and_truncation_detected():
    container, _ = cs.create_vault(_DATA, "pw", _PRESET)
    tampered = bytearray(container)
    tampered[-5] ^= 0xFF
    with pytest.raises(cs.CorruptVault):
        cs.unlock(bytes(tampered), "pw")


def test_swapping_header_between_vaults_detected():
    """Заголовок/обёртки от другого контейнера с тем же паролем: AAD не сойдётся."""
    a, _ = cs.create_vault(_DATA, "pw", _PRESET)
    b, _ = cs.create_vault(_DATA, "pw", _PRESET)
    hdr_len = len(cs.MAGIC) + 1 + 1 + 1 + 1 + 4 + 1 + 16 + 2 + 60 + 16 + 2 + 60
    franken = b[:hdr_len] + a[hdr_len:]
    with pytest.raises((cs.CorruptVault, cs.WrongPassword)):
        cs.unlock(franken, "pw")


def test_nfc_password_new_vault_accepts_nfd_input():
    composed = unicodedata.normalize("NFC", "пароль-é")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    container, _ = cs.create_vault(_DATA, composed, _PRESET)
    assert cs.unlock(container, decomposed)[0] == _DATA
    assert cs.unlock(container, composed)[0] == _DATA


def test_legacy_vault_keeps_raw_encoding_and_nfc_fallback():
    composed = unicodedata.normalize("NFC", "café-pw")
    decomposed = unicodedata.normalize("NFD", composed)
    # Старый пароль задан в NFD (как набрали на другой ОС), в v1 - сырой UTF-8.
    v1, _ = _make_v1(_DATA, decomposed)
    assert cs.unlock(v1, decomposed)[0] == _DATA            # сырое совпадение
    v1c, _ = _make_v1(_DATA, composed)
    assert cs.unlock(v1c, decomposed)[0] == _DATA           # запасной NFC
    with pytest.raises(cs.WrongPassword):
        cs.unlock(v1c, "wrong")


def test_change_password_sets_nfc_flag_on_legacy():
    v1, _ = _make_v1(_DATA, "old")
    _, dek, header = cs.unlock(v1, "old")
    new_header = cs.change_password(None, dek, header, "new-é")
    assert new_header["flags"] & cs.FLAG_PW_NFC
    out, _, _ = cs.unlock(cs.seal(_DATA, dek, new_header), "new-é")
    assert out == _DATA


def test_recovery_hkdf_for_new_and_regenerated():
    container, rec = cs.create_vault(_DATA, "pw", _PRESET)
    assert cs.unlock(container, rec, is_recovery=True)[0] == _DATA
    # Нормализация ввода (регистр/разделители) сохраняется.
    assert cs.unlock(container, rec.lower().replace("-", " "), is_recovery=True)[0] == _DATA
    # Legacy v1 -> regenerate переводит recovery на HKDF.
    v1, _ = _make_v1(_DATA, "pw")
    _, dek, header = cs.unlock(v1, "pw")
    h2, new_rec = cs.regenerate_recovery(dek, header)
    assert h2["flags"] & cs.FLAG_REC_HKDF
    assert cs.unlock(cs.seal(_DATA, dek, h2), new_rec, is_recovery=True)[0] == _DATA


def test_rotate_dek_changes_key_and_recovery():
    container, rec = cs.create_vault(_DATA, "pw", _PRESET)
    _, dek, header = cs.unlock(container, "pw")
    new_header, new_dek, new_rec = cs.change_password(
        None, dek, header, "pw2", rotate_dek=True)
    assert new_dek != dek and new_rec != rec
    new_container = cs.seal(_DATA, new_dek, new_header)
    assert cs.unlock(new_container, "pw2")[0] == _DATA
    assert cs.unlock(new_container, new_rec, is_recovery=True)[0] == _DATA
    with pytest.raises(cs.WrongPassword):
        cs.unlock(new_container, "pw")
    with pytest.raises(cs.WrongPassword):
        cs.unlock(new_container, rec, is_recovery=True)
    # Старая wrap-пара + новый data-раздел: ключ данных другой.
    mixed = cs._header_bytes(header) + new_container[len(cs._header_bytes(new_header)):]
    with pytest.raises(cs.CorruptVault):
        cs.unlock(mixed, "pw")


def test_seal_parts_equals_seal_layout():
    container, _ = cs.create_vault(_DATA, "pw", _PRESET)
    _, dek, header = cs.unlock(container, "pw")
    hdr, nonce, ct = cs.seal_parts(_DATA, dek, header)
    assert cs.unlock(hdr + nonce + ct, "pw")[0] == _DATA


# ─── Database-уровень: B13, S8, S2 ───────────────────────────────────────────

def _enc_db(tmp_db_path):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    d.add_service("S")
    d.enable_encryption("pw", _PRESET)
    return d


def test_dek_is_bytearray_and_zeroed_on_lock(tmp_db_path):
    d = _enc_db(tmp_db_path)
    dek_ref = d._dek
    assert isinstance(dek_ref, bytearray) and any(dek_ref)
    d.lock()
    assert d._dek is None
    assert not any(dek_ref)          # исходный буфер занулён in-place
    d.shutdown_executor()


def test_seal_after_lock_raises_stale_not_typeerror(tmp_db_path):
    d = _enc_db(tmp_db_path)
    db_bytes = d.serialize_db()
    d.lock()
    with pytest.raises(StaleSessionError):
        d.seal_and_write(db_bytes)
    d.shutdown_executor()


def test_seal_started_before_lock_uses_captured_key(tmp_db_path, monkeypatch):
    """lock() посреди seal_and_write не должен испортить контейнер."""
    d = _enc_db(tmp_db_path)
    db_bytes = d.serialize_db()
    real_parts = cs.seal_parts
    locked = threading.Event()

    def seal_after_lock(data, dek, header):
        monkeypatch.setattr(cs, "seal_parts", real_parts)
        d.lock(force=True)           # key занулён/сброшен ДО шифрования
        locked.set()
        return real_parts(data, dek, header)

    monkeypatch.setattr(cs, "seal_parts", seal_after_lock)
    d.seal_and_write(db_bytes, force=True)
    assert locked.is_set()
    with open(tmp_db_path, "rb") as f:
        out, _, _ = cs.unlock(f.read(), "pw")
    assert out == db_bytes
    d.shutdown_executor()


def test_change_master_password_rotate_dek(tmp_db_path):
    d = _enc_db(tmp_db_path)
    with open(tmp_db_path, "rb") as f:
        old_container = f.read()
    new_rec = d.change_master_password("pw-new", _PRESET, rotate_dek=True)
    assert new_rec and "-" in new_rec
    with open(tmp_db_path, "rb") as f:
        new_container = f.read()
    assert cs.unlock(new_container, "pw-new")[0]
    assert cs.unlock(new_container, new_rec, is_recovery=True)[0]
    with pytest.raises(cs.WrongPassword):
        cs.unlock(new_container, "pw")
    assert cs.unlock(old_container, "pw")[0]      # старый файл - старый ключ
    assert d.verify_password("pw-new") and not d.verify_password("pw")
    d.lock()
    d.shutdown_executor()


def test_rotate_dek_rolls_back_on_write_failure(tmp_db_path, monkeypatch):
    d = _enc_db(tmp_db_path)
    old_dek, old_header = bytes(d._dek), d._header

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(d, "_write_container", boom)
    with pytest.raises(OSError):
        d.change_master_password("pw-new", _PRESET, rotate_dek=True)
    assert bytes(d._dek) == old_dek and d._header is old_header
    d.close(persist=False)
    d.shutdown_executor()


def test_change_master_password_default_returns_none(tmp_db_path):
    d = _enc_db(tmp_db_path)
    assert d.change_master_password("pw2", _PRESET) is None
    assert d.verify_password("pw2")
    d.lock()
    d.shutdown_executor()


# Контейнер, созданный РЕАЛЬНЫМ кодом v1 (HEAD до аудита): пароль «пароль».
_REAL_V1_HEX = (
    "48524e4b763101000200800000024ef3bd33f0d8637c35a1c5b54cf1ff4e3c00583fe8752cccb3b078057bbe5b718ff5a4cae83b462199ea459061ac844654a70881a217b7fa3c4fdd032ee72c8795b37730cdc765ebb31d7a485c9af482faa57d7e201d29be4e0f78edd94d3c00565af53bab9f09879467db8bbedf992f62c20e1ed34c2e484a9ef6219e86c5cf430fa11337ebe5d980ac0bc1c61f8cee35560f320295be7b091069170ce30832e7cb2f717da73e59b04d52179d9b8f2ea8c6a52657c8b87fa262df690df390215a4249ccb21b")
_REAL_V1_RECOVERY = "OM6J-UYYL-RTAQ-A2WJ-CNK5-IYAQ-TT4T-P7G7"


def test_real_v1_file_from_old_code_unlocks():
    v1 = bytes.fromhex(_REAL_V1_HEX)
    assert cs.unlock(v1, "пароль")[0] == b"legacy-v1-data"
    assert cs.unlock(v1, _REAL_V1_RECOVERY.lower(), is_recovery=True)[0] == b"legacy-v1-data"
