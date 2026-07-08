"""Шифрованный режим Database и атомарная запись (Эпик 1).

Проверяем: round-trip enable/disable шифрования через Database, что persist в
шифр. режиме не оставляет временных файлов и контейнер переоткрывается, и что
_atomic_replace атомарно подменяет файл без «хвостов» .tmp.
"""
import os

import pytest

from hranilka.crypto import store as cs
from hranilka.data.database import Database, VaultConflictError

_PW = "correct horse battery staple"
_PRESET = "fast"   # быстрый Argon2 для тестов


def _leftover_temps(directory):
    return [n for n in os.listdir(directory)
            if n.endswith(".tmp") or n.startswith(".hranilka-")]


def test_enable_encryption_roundtrip(tmp_db_path):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    sid = d.add_service("S")
    d.add_account(sid, "Acc")
    recovery = d.enable_encryption(_PW, _PRESET)
    assert d.encrypted is True
    assert recovery and "-" in recovery
    # На диске — именно зашифрованный контейнер, а не SQLite.
    assert cs.is_encrypted_file(tmp_db_path) is True
    # Никаких временных файлов после записи контейнера.
    assert _leftover_temps(os.path.dirname(tmp_db_path)) == []
    d.close()

    # Переоткрытие зашифрованного файла мастер-паролем — данные на месте.
    with open(tmp_db_path, "rb") as f:
        container = f.read()
    db_bytes, dek, header = cs.unlock(container, _PW)
    d2 = Database(tmp_db_path)
    d2.open_encrypted(db_bytes, dek, header)
    d2.create_tables()
    assert [s["name"] for s in d2.get_services()] == ["S"]
    d2.close(persist=False)


def test_disable_encryption_back_to_plaintext(tmp_db_path):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    d.add_folder("F")
    d.enable_encryption(_PW, _PRESET)
    d.disable_encryption()
    assert d.encrypted is False
    assert cs.is_encrypted_file(tmp_db_path) is False
    assert [f["name"] for f in d.get_folders()] == ["F"]
    assert _leftover_temps(os.path.dirname(tmp_db_path)) == []
    d.close(persist=False)


def test_encrypted_persist_no_leftover(tmp_db_path):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    d.enable_encryption(_PW, _PRESET)
    sid = d.add_service("S1")
    d.add_account(sid, "A1")
    d.flush()                      # сброс отложенного снимка на диск
    assert _leftover_temps(os.path.dirname(tmp_db_path)) == []
    d.close()

    with open(tmp_db_path, "rb") as f:
        db_bytes, _, _ = cs.unlock(f.read(), _PW)
    d2 = Database(tmp_db_path)
    d2.conn = __import__("sqlite3").connect(":memory:")
    d2.conn.deserialize(db_bytes)
    d2._setup_conn()
    assert d2.get_services()[0]["name"] == "S1"
    d2.conn.close()


# ─── H-11: смена мастер-пароля, откат заголовка, lock-цикл, конфликт файла ────

def _open_encrypted_db(tmp_db_path, password=_PW):
    """Создать зашифрованную БД с одним сервисом и вернуть открытый Database."""
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    d.add_service("S")
    d.enable_encryption(password, _PRESET)
    return d


def test_change_master_password_old_rejected_new_accepted(tmp_db_path):
    """После смены пароля старый не открывает переоткрытый контейнер, новый — да,
    данные целы."""
    d = _open_encrypted_db(tmp_db_path)
    d.change_master_password("new-master-pass", _PRESET)
    d.close()

    with open(tmp_db_path, "rb") as f:
        container = f.read()
    with pytest.raises(cs.WrongPassword):
        cs.unlock(container, _PW)
    db_bytes, dek, header = cs.unlock(container, "new-master-pass")
    d2 = Database(tmp_db_path)
    d2.open_encrypted(db_bytes, dek, header)
    d2.create_tables()
    assert [s["name"] for s in d2.get_services()] == ["S"]
    d2.close(persist=False)


def test_set_header_rolls_back_on_write_failure(tmp_db_path, monkeypatch):
    """H5-02: если запись контейнера падает, self._header откатывается к прежнему,
    исключение пробрасывается — заголовок в памяти согласован с диском."""
    d = _open_encrypted_db(tmp_db_path)
    old_header = d._header
    new_header = cs.change_password(None, d._dek, d._header, "another-pass")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(d, "_write_container", boom)
    with pytest.raises(OSError):
        d.set_header(new_header)
    # Заголовок остался прежним — рассогласования с диском нет.
    assert d._header is old_header
    d.close(persist=False)


def test_lock_then_reunlock_restores_data(tmp_db_path):
    """lock() обнуляет ключ и рвёт соединение (CRUD падает); повторный unlock +
    open_encrypted восстанавливает доступ к данным."""
    d = _open_encrypted_db(tmp_db_path)
    d.lock()
    assert d._dek is None
    assert d.conn is None
    with pytest.raises(Exception):
        d.get_services()            # соединения нет — CRUD невозможен

    with open(tmp_db_path, "rb") as f:
        db_bytes, dek, header = cs.unlock(f.read(), _PW)
    d.open_encrypted(db_bytes, dek, header)
    assert [s["name"] for s in d.get_services()] == ["S"]
    d.close(persist=False)


def test_persist_raises_vault_conflict_on_external_change(tmp_db_path):
    """Файл-контейнер изменён извне (другие байты → др. mtime/размер) →
    persist() поднимает VaultConflictError, защищая чужие изменения."""
    d = _open_encrypted_db(tmp_db_path)
    d.flush()                                   # зафиксировать текущий контейнер
    # Внешняя подмена файла: пишем ЧУЖОЙ (но валидный) контейнер большего размера.
    other, _ = cs.create_vault(b"foreign-vault-bytes" * 4, "other-pw", _PRESET)
    with open(tmp_db_path, "wb") as f:
        f.write(other)
    d.add_service("S2")                         # локальное изменение → нужно persist
    with pytest.raises(VaultConflictError):
        d.persist()
    d.close(persist=False)


# ─── H-11: verify_secret ─────────────────────────────────────────────────────

def test_verify_secret_password_and_recovery(tmp_db_path):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    recovery = d.enable_encryption(_PW, _PRESET)
    assert d.verify_secret(_PW) is True
    assert d.verify_secret("wrong-pass") is False
    assert d.verify_secret(recovery, is_recovery=True) is True
    assert d.verify_secret("AAAA-BBBB-CCCC-DDDD", is_recovery=True) is False
    d.close(persist=False)


def test_verify_secret_plaintext_mode_false(db):
    """В обычном (незашифрованном) режиме verify_secret всегда False."""
    assert db.verify_secret("anything") is False
    assert db.verify_secret("anything", is_recovery=True) is False


def test_atomic_replace_overwrites_without_temp(tmp_path):
    # _atomic_replace применяется к файлу, который не держит открытым SQLite
    # (шифр. режим — БД в :memory:; вкл/выкл шифрования — соединение уже закрыто).
    p = str(tmp_path / "file.bin")
    d = Database(p)
    d._atomic_replace(b"v1")
    with open(p, "rb") as f:
        assert f.read() == b"v1"
    d._atomic_replace(b"v2 is longer")
    with open(p, "rb") as f:
        assert f.read() == b"v2 is longer"
    assert _leftover_temps(str(tmp_path)) == []
