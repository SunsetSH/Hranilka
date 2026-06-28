"""Шифр-контейнер: roundtrip, строгий разбор заголовка (H-04), неверный секрет."""
import pytest

import crypto_store as cs

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
