"""Пакет миграций (hranilka/data/database/migrations): реестр нумерованных
шагов, конвейер runner, совместимость legacy-diff.
"""
import pytest

from hranilka.data.database import migrations
from hranilka.data.database.migrations import runner
from hranilka.data.database.schema import SCHEMA_VERSION
from hranilka.data.errors import FutureSchemaError


def test_registry_covers_legacy_base_plus_one():
    """Реестр покрывает ровно версии LEGACY_BASE+1..SCHEMA_VERSION.
    Зарегистрированы шаги m009 (fin-таблицы), m010 (чистка bank_account),
    m011 (чистка ewallet) и m012 (VPS-серверы)."""
    assert migrations.LEGACY_BASE == 8
    assert SCHEMA_VERSION == 12
    assert set(migrations.MIGRATIONS) == set(
        range(migrations.LEGACY_BASE + 1, SCHEMA_VERSION + 1))


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
    # Реестр обязан покрывать весь диапазон LEGACY_BASE+1..SCHEMA_VERSION+2:
    # реальные шаги (ключ 9) + два фейковых. Шаги ≤ текущей версии БД не
    # выполняются (db на SCHEMA_VERSION), поэтому в calls только +1 и +2.
    fake = {v: (lambda conn: None) for v in migrations.MIGRATIONS}
    fake[SCHEMA_VERSION + 1] = lambda conn: calls.append(SCHEMA_VERSION + 1)
    fake[SCHEMA_VERSION + 2] = lambda conn: calls.append(SCHEMA_VERSION + 2)
    monkeypatch.setattr(runner, "MIGRATIONS", fake)
    runner.run(db)
    assert calls == [SCHEMA_VERSION + 1, SCHEMA_VERSION + 2]
    assert db.get_schema_version() == SCHEMA_VERSION + 2


def test_numbered_step_not_rerun_from_reached_version(db, monkeypatch):
    """Шаги ниже текущей версии БД не выполняются повторно."""
    calls = []
    monkeypatch.setattr(runner, "SCHEMA_VERSION", SCHEMA_VERSION + 2)
    fake = {v: (lambda conn: None) for v in migrations.MIGRATIONS}
    fake[SCHEMA_VERSION + 1] = lambda conn: calls.append(SCHEMA_VERSION + 1)
    fake[SCHEMA_VERSION + 2] = lambda conn: calls.append(SCHEMA_VERSION + 2)
    monkeypatch.setattr(runner, "MIGRATIONS", fake)
    db.set_schema_version(SCHEMA_VERSION + 1)   # первый шаг уже применён
    runner.run(db)
    assert calls == [SCHEMA_VERSION + 2]
    assert db.get_schema_version() == SCHEMA_VERSION + 2


# ─── m012: v11 → v12 (VPS-серверы) ─────────────────────────────────────────

def _make_v11_db(path):
    """Sqlite-файл со схемой v11 (fin-таблицы есть, server-таблиц ещё нет)."""
    import sqlite3
    from hranilka.data.database.schema import DbSchemaMixin

    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE folders (id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sort_order INTEGER DEFAULT 0);
        CREATE TABLE services (id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, folder_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sort_order INTEGER DEFAULT 0);
        CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    con.execute(DbSchemaMixin._accounts_create_sql("accounts"))
    con.execute(DbSchemaMixin._linked_accounts_create_sql("linked_accounts"))
    con.execute(DbSchemaMixin._fin_items_create_sql())
    con.execute(DbSchemaMixin._fin_links_create_sql())
    con.execute(DbSchemaMixin._fin_gallery_create_sql())
    for stmt in DbSchemaMixin._fin_index_sql():
        con.execute(stmt)
    con.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '11')")
    con.execute("INSERT INTO services (name) VALUES ('Провайдер')")
    con.execute(
        "INSERT INTO accounts (service_id, account_name, login, password) "
        "VALUES (1, 'Акк', 'user', 'secret')")
    con.commit()
    con.close()


def test_v11_db_migrates_to_v12_creates_server_tables(tmp_path, monkeypatch):
    """База v11 (без server-таблиц) → открытие → v12: таблицы servers/
    server_links/server_gallery созданы, старые данные (аккаунт) целы,
    страховочная копия базы (.pre-migrate*) создана перед миграцией (H7-03;
    удаляется в конце успешного прогона — перехватываем через monkeypatch,
    чтобы убедиться, что она вообще была создана)."""
    import glob
    from hranilka.data.database import Database

    path = str(tmp_path / "v11.db")
    _make_v11_db(path)
    d = Database(path)
    d.connect()

    created_copies = []
    real_finish = type(d)._finish_premigration_backup

    def spy_finish(self, copy_path):
        if copy_path:
            created_copies.append(copy_path)
        return real_finish(self, copy_path)

    monkeypatch.setattr(type(d), "_finish_premigration_backup", spy_finish)

    try:
        d.create_tables()
        assert d.get_schema_version() == SCHEMA_VERSION == 12
        assert {"servers", "server_links", "server_gallery"} <= d._table_names()
        d.cursor.execute("SELECT account_name, login, password FROM accounts")
        row = d.cursor.fetchone()
        assert (row["account_name"], row["login"], row["password"]) == \
               ("Акк", "user", "secret")
        # H7-03: страховочная копия была создана перед миграцией версии
        # (и удалена best_effort_wipe после успеха — на диске её уже нет).
        assert created_copies
        assert not glob.glob(path + ".pre-migrate*")
    finally:
        d.close(persist=False)

    # Повторное открытие уже мигрированной базы — идемпотентно (fast path).
    d2 = Database(path)
    d2.connect()
    try:
        d2.create_tables()
        assert d2.get_schema_version() == SCHEMA_VERSION
        assert {"servers", "server_links", "server_gallery"} <= d2._table_names()
    finally:
        d2.close(persist=False)


def test_m012_idempotent(tmp_path):
    """Повторный прогон m012 на той же базе не падает (CREATE ... IF NOT EXISTS)."""
    import sqlite3
    from hranilka.data.database.migrations.m012_servers import migrate as m012

    path = str(tmp_path / "v11.db")
    _make_v11_db(path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        m012(con)
        m012(con)                       # второй раз — no-op, без ошибок
        con.commit()
        names = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"servers", "server_links", "server_gallery"} <= names
        idx = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert "uq_server_link" in idx
    finally:
        con.close()
