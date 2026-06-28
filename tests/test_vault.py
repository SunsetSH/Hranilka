"""Шифрованный режим Database и атомарная запись (Эпик 1).

Проверяем: round-trip enable/disable шифрования через Database, что persist в
шифр. режиме не оставляет временных файлов и контейнер переоткрывается, и что
_atomic_replace атомарно подменяет файл без «хвостов» .tmp.
"""
import os

import crypto_store as cs
from database import Database

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
