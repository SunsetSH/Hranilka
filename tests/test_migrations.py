"""Пакет миграций (hranilka/data/migrations): реестр нумерованных шагов,
конвейер runner, совместимость legacy-diff.
"""
import pytest

from hranilka.data import migrations
from hranilka.data.errors import FutureSchemaError
from hranilka.data.migrations import runner
from hranilka.data.schema import SCHEMA_VERSION


def test_registry_empty_at_legacy_base():
    """Пока SCHEMA_VERSION == LEGACY_BASE нумерованных шагов нет."""
    assert SCHEMA_VERSION == migrations.LEGACY_BASE
    assert migrations.MIGRATIONS == {}


def test_run_noop_on_current_version(db):
    ver_before = db.get_schema_version()
    runner.run(db)
    assert db.get_schema_version() == ver_before == SCHEMA_VERSION


def test_run_rejects_future_schema(db):
    db.set_schema_version(SCHEMA_VERSION + 1)
    with pytest.raises(FutureSchemaError):
        runner.run(db)


def test_run_legacy_diff_upgrades_old_version(db):
    """База «старой» версии догоняется до актуальной (legacy-diff идемпотентен)."""
    db.set_schema_version(SCHEMA_VERSION - 3)
    db._commit()
    runner.run(db)
    assert db.get_schema_version() == SCHEMA_VERSION


def test_v0_db_without_new_columns_migrates(tmp_path):
    """Регрессия (латентный баг до этапа 3): в базе v0-стиля без deleted_at
    CREATE INDEX idx_accounts_deleted падал, т.к. diff колонок выполнялся
    только в _migrate() — уже ПОСЛЕ создания индексов."""
    import sqlite3
    from hranilka.data.database import Database

    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE folders (id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL);
        CREATE TABLE services (id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, folder_id INTEGER);
        CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_id INTEGER NOT NULL, account_name TEXT NOT NULL,
            login TEXT, password TEXT);
        INSERT INTO services (name) VALUES ('Сервис');
        INSERT INTO accounts (service_id, account_name, login)
            VALUES (1, 'Акк', 'user');
    """)
    con.commit()
    con.close()

    d = Database(path)
    d.connect()
    try:
        d.create_tables()
        assert d.get_schema_version() == SCHEMA_VERSION
        assert {"url", "notes", "deleted_at"} <= d._get_columns("accounts")
        d.cursor.execute("SELECT account_name, login FROM accounts")
        row = d.cursor.fetchone()
        assert (row["account_name"], row["login"]) == ("Акк", "user")
    finally:
        d.close(persist=False)


def test_registry_gap_detected(db, monkeypatch):
    """Подняли SCHEMA_VERSION, но не зарегистрировали шаг — громкая ошибка."""
    monkeypatch.setattr(runner, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
    db.set_schema_version(SCHEMA_VERSION)  # теперь «старая» относительно runner
    with pytest.raises(RuntimeError, match="Реестр миграций"):
        runner.run(db)


def test_numbered_step_runs_in_order(db, monkeypatch):
    """Нумерованные шаги выполняются по порядку и версия обновляется."""
    calls = []
    monkeypatch.setattr(runner, "SCHEMA_VERSION", SCHEMA_VERSION + 2)
    monkeypatch.setattr(runner, "MIGRATIONS", {
        SCHEMA_VERSION + 1: lambda conn: calls.append(SCHEMA_VERSION + 1),
        SCHEMA_VERSION + 2: lambda conn: calls.append(SCHEMA_VERSION + 2),
    })
    runner.run(db)
    assert calls == [SCHEMA_VERSION + 1, SCHEMA_VERSION + 2]
    assert db.get_schema_version() == SCHEMA_VERSION + 2


def test_numbered_step_not_rerun_from_reached_version(db, monkeypatch):
    """Шаги ниже текущей версии БД не выполняются повторно."""
    calls = []
    monkeypatch.setattr(runner, "SCHEMA_VERSION", SCHEMA_VERSION + 2)
    monkeypatch.setattr(runner, "MIGRATIONS", {
        SCHEMA_VERSION + 1: lambda conn: calls.append(SCHEMA_VERSION + 1),
        SCHEMA_VERSION + 2: lambda conn: calls.append(SCHEMA_VERSION + 2),
    })
    db.set_schema_version(SCHEMA_VERSION + 1)   # первый шаг уже применён
    runner.run(db)
    assert calls == [SCHEMA_VERSION + 2]
    assert db.get_schema_version() == SCHEMA_VERSION + 2
