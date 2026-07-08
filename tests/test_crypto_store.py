"""Шифр-контейнер: roundtrip, строгий разбор заголовка (H-04), неверный секрет."""
import pytest

from hranilka.crypto import store as cs

# Быстрый preset, чтобы Argon2 не тормозил тесты.
_PW = "correct horse battery staple"


def test_create_unlock_roundtrip():
    data = b"hello sqlite bytes" * 100
    container, recovery = cs.create_vault(data, _PW, preset="fast")
    assert container.startswith(cs.MAGIC)
    db_bytes, dek, header = cs.unlock(container, _PW)
    assert db_bytes == data
    # recovery-код тоже расшифровывает
    db2, _, _ = cs.unlock(container, recovery, is_recovery=True)
    assert db2 == data


def test_wrong_password_raises():
    container, _ = cs.create_vault(b"data", _PW, preset="fast")
    with pytest.raises(cs.WrongPassword):
        cs.unlock(container, "wrong password")


def test_parse_rejects_magic_only():
    # b"HRNKv1" раньше проходил как «валидный» бэкап — теперь должен падать.
    with pytest.raises(cs.CorruptVault):
        cs._parse(cs.MAGIC)


def test_parse_rejects_garbage():
    with pytest.raises(Exception):
        cs._parse(b"not a vault at all")


def test_is_encrypted_file(tmp_path):
    enc = tmp_path / "enc.bin"
    container, _ = cs.create_vault(b"x", _PW, preset="fast")
    enc.write_bytes(container)
    assert cs.is_encrypted_file(str(enc)) is True
    plain = tmp_path / "plain.bin"
    plain.write_bytes(b"SQLite format 3\x00")
    assert cs.is_encrypted_file(str(plain)) is False


# ─── H-11: смена пароля/recovery и целостность GCM ───────────────────────────

_NEW_PW = "totally different passphrase 42"
_DATA = b"\x00SQLite format 3\x00" + b"payload-bytes" * 8


def _make():
    """Свежий контейнер + recovery-код на тестовых данных."""
    return cs.create_vault(_DATA, _PW, preset="fast")


def test_change_password_old_rejected_new_unlocks():
    container, recovery = _make()
    _, dek, header = cs.unlock(container, _PW)

    new_header = cs.change_password(container, dek, header, _NEW_PW)
    new_container = cs.seal(_DATA, dek, new_header)

    # Старый пароль больше не открывает пересобранный контейнер.
    with pytest.raises(cs.WrongPassword):
        cs.unlock(new_container, _PW)
    # Новый пароль открывает, данные целы.
    db_bytes, _, _ = cs.unlock(new_container, _NEW_PW)
    assert db_bytes == _DATA
    # Recovery-код продолжает работать (перезаворачивался только wrap_pw).
    rec_bytes, _, _ = cs.unlock(new_container, recovery, is_recovery=True)
    assert rec_bytes == _DATA


def test_change_password_can_switch_preset():
    container, _ = _make()
    _, dek, header = cs.unlock(container, _PW)
    new_header = cs.change_password(container, dek, header, _NEW_PW, preset="balanced")
    assert new_header["preset"] == "balanced"
    assert new_header["params"] == cs.PRESETS["balanced"]
    db_bytes, _, _ = cs.unlock(cs.seal(_DATA, dek, new_header), _NEW_PW)
    assert db_bytes == _DATA


def test_regenerate_recovery_old_rejected_new_works():
    container, old_recovery = _make()
    _, dek, header = cs.unlock(container, _PW)

    new_header, new_recovery = cs.regenerate_recovery(dek, header)
    assert new_recovery != old_recovery
    new_container = cs.seal(_DATA, dek, new_header)

    # Старый recovery-код отвергается.
    with pytest.raises(cs.WrongPassword):
        cs.unlock(new_container, old_recovery, is_recovery=True)
    # Новый recovery-код открывает, данные целы.
    rec_bytes, _, _ = cs.unlock(new_container, new_recovery, is_recovery=True)
    assert rec_bytes == _DATA
    # Пароль по-прежнему работает (wrap_pw не трогали).
    pw_bytes, _, _ = cs.unlock(new_container, _PW)
    assert pw_bytes == _DATA


def _flip_byte(data: bytes, index: int) -> bytes:
    b = bytearray(data)
    b[index] ^= 0xFF
    return bytes(b)


def test_tamper_data_section_detected():
    """Инверсия байта в шифртексте данных → провал тега GCM (WrongPassword)."""
    container, _ = _make()
    # Данные лежат в самом конце файла — правим последний байт (data_ct/тег).
    tampered = _flip_byte(container, len(container) - 1)
    with pytest.raises(cs.WrongPassword):
        cs.unlock(tampered, _PW)


def test_tamper_wrap_pw_detected():
    """Инверсия байта в wrap_pw → провал при снятии DEK паролем.

    Recovery-код остаётся рабочим (wrap_rec не тронут)."""
    container, recovery = _make()
    # Смещение wrap_pw: MAGIC(6)+VER(1)+preset(1)+t(1)+m(4)+p(1)+salt_pw(16)
    # +wrap_pw_len(2) → далее wrap_pw (см. формат контейнера в crypto_store).
    off = len(cs.MAGIC) + 1 + 1 + 1 + 4 + 1 + cs.SALT_LEN + 2
    tampered = _flip_byte(container, off + 5)   # байт внутри wrap_pw
    with pytest.raises((cs.WrongPassword, cs.CorruptVault)):
        cs.unlock(tampered, _PW)
    # Recovery-путь не задет подделкой wrap_pw.
    rec_bytes, _, _ = cs.unlock(tampered, recovery, is_recovery=True)
    assert rec_bytes == _DATA


def test_truncated_container_is_corrupt():
    """Обрезанный контейнер не проходит структурный разбор → CorruptVault."""
    container, _ = _make()
    truncated = container[: len(cs.MAGIC) + 20]
    with pytest.raises(cs.CorruptVault):
        cs.unlock(truncated, _PW)
