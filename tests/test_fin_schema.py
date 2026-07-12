"""Схема БД и миграции финансовых сущностей: апгрейд базы v8 → актуальной
(m009 создаёт fin-таблицы), чистка bank_account миграцией m010 (v9 → v10),
чистка ewallet миграцией m011 (v10 → v11), идемпотентность, отказ на будущей
версии, контроль реестра миграций.

Фикстуры старых версий строятся программно: sqlite с DDL нужной версии и
app_meta.schema_version.
"""
import sqlite3

import pytest

from hranilka.data.database import Database, SCHEMA_VERSION, FutureSchemaError
from hranilka.data.database.migrations import runner
from hranilka.data.database.migrations.m009_fin_items import migrate as m009
from hranilka.data.database.schema import DbSchemaMixin

_FIN_TABLES = ("fin_items", "fin_links", "fin_gallery")


def _make_v8_db(path, *, version=8):
    """Создаёт sqlite-файл со схемой v8 (без финансовых таблиц) и данными."""
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
    # accounts / linked_accounts — из единого DDL схемы (без дрейфа).
    con.execute(DbSchemaMixin._accounts_create_sql("accounts"))
    con.execute(DbSchemaMixin._linked_accounts_create_sql("linked_accounts"))
    con.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', ?)",
                (str(version),))
    con.execute("INSERT INTO services (name) VALUES ('Банк')")
    con.execute(
        "INSERT INTO accounts (service_id, account_name, login, password) "
        "VALUES (1, 'Акк', 'user', 'secret')")
    con.commit()
    con.close()


def _make_v9_db(path, *, version=9):
    """Создаёт sqlite-файл со схемой v9 (fin-таблицы есть) и данными: сервис,
    аккаунт, живая карта и запись типа bank_account, у обеих — связь с аккаунтом
    и строка галереи. Служит фикстурой для проверки миграции m010."""
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
    # Финансовые таблицы v9 — из единого DDL схемы (тот же источник, что m009).
    con.execute(DbSchemaMixin._fin_items_create_sql())
    con.execute(DbSchemaMixin._fin_links_create_sql())
    con.execute(DbSchemaMixin._fin_gallery_create_sql())
    for stmt in DbSchemaMixin._fin_index_sql():
        con.execute(stmt)
    con.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', ?)",
                (str(version),))
    con.execute("INSERT INTO services (name) VALUES ('Банк')")
    con.execute(
        "INSERT INTO accounts (service_id, account_name, login, password) "
        "VALUES (1, 'Акк', 'user', 'secret')")
    # id 1 — живая карта, id 2 — удаляемый bank_account.
    con.execute("INSERT INTO fin_items (id, service_id, item_type, name, data) "
                "VALUES (1, 1, 'bank_card', 'Карта', '{}')")
    con.execute("INSERT INTO fin_items (id, service_id, item_type, name, data) "
                "VALUES (2, 1, 'bank_account', 'Счёт', '{}')")
    con.execute("INSERT INTO fin_links (item_id, account_id) VALUES (1, 1)")
    con.execute("INSERT INTO fin_links (item_id, account_id) VALUES (2, 1)")
    con.execute("INSERT INTO fin_gallery (item_id, description) VALUES (1, 'чек')")
    con.execute("INSERT INTO fin_gallery (item_id, description) VALUES (2, 'скан')")
    con.commit()
    con.close()


def _make_v10_db(path, *, version=10):
    """Создаёт sqlite-файл со схемой v10 (fin-таблицы есть, bank_account уже
    вычищен) и данными: живая карта, криптокошелёк и удаляемый ewallet — у всех
    связь с аккаунтом и строка галереи. Фикстура для проверки миграции m011."""
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
    # Финансовые таблицы — из единого DDL схемы (тот же источник, что m009).
    con.execute(DbSchemaMixin._fin_items_create_sql())
    con.execute(DbSchemaMixin._fin_links_create_sql())
    con.execute(DbSchemaMixin._fin_gallery_create_sql())
    for stmt in DbSchemaMixin._fin_index_sql():
        con.execute(stmt)
    con.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', ?)",
                (str(version),))
    con.execute("INSERT INTO services (name) VALUES ('Банк')")
    con.execute(
        "INSERT INTO accounts (service_id, account_name, login, password) "
        "VALUES (1, 'Акк', 'user', 'secret')")
    # id 1 — карта, id 2 — криптокошелёк (целы), id 3 — удаляемый ewallet.
    con.execute("INSERT INTO fin_items (id, service_id, item_type, name, data) "
                "VALUES (1, 1, 'bank_card', 'Карта', '{}')")
    con.execute("INSERT INTO fin_items (id, service_id, item_type, name, data) "
                "VALUES (2, 1, 'crypto_wallet', 'Кошелёк', '{}')")
    con.execute("INSERT INTO fin_items (id, service_id, item_type, name, data) "
                "VALUES (3, 1, 'ewallet', 'Электронный', '{}')")
    for iid, desc in ((1, "чек"), (2, "фото"), (3, "скан")):
        con.execute("INSERT INTO fin_links (item_id, account_id) VALUES (?, 1)",
                    (iid,))
        con.execute("INSERT INTO fin_gallery (item_id, description) VALUES (?, ?)",
                    (iid, desc))
    con.commit()
    con.close()


def _open(path):
    d = Database(path)
    d.connect()
    return d


def test_v8_db_migrates_to_current(tmp_path):
    """База v8 с данными → открытие → актуальная версия: fin-таблицы созданы,
    аккаунты целы."""
    path = str(tmp_path / "v8.db")
    _make_v8_db(path)
    d = _open(path)
    try:
        d.create_tables()
        assert d.get_schema_version() == SCHEMA_VERSION == 11
        tables = d._table_names()
        assert set(_FIN_TABLES) <= tables
        d.cursor.execute("SELECT account_name, login, password FROM accounts")
        row = d.cursor.fetchone()
        assert (row["account_name"], row["login"], row["password"]) == \
               ("Акк", "user", "secret")
    finally:
        d.close(persist=False)


def test_empty_db_is_current(db):
    """Пустая новая база создаётся сразу на актуальной версии с fin-таблицами."""
    assert db.get_schema_version() == SCHEMA_VERSION == 11
    assert set(_FIN_TABLES) <= db._table_names()


def test_future_version_rejected(tmp_path):
    """База версии новее поддерживаемой → FutureSchemaError (миграция вниз
    недопустима)."""
    path = str(tmp_path / "future.db")
    _make_v8_db(path, version=SCHEMA_VERSION + 1)
    d = _open(path)
    try:
        with pytest.raises(FutureSchemaError):
            d.create_tables()
    finally:
        d.close(persist=False)


def test_v9_db_migrates_to_v10_drops_bank_account(tmp_path):
    """База v9 с записью bank_account → открытие → актуальная версия: запись
    удалена, её связи и галерея подчищены каскадом (FK), карта и её
    связь/галерея целы."""
    path = str(tmp_path / "v9.db")
    _make_v9_db(path)
    d = _open(path)
    try:
        d.create_tables()
        assert d.get_schema_version() == SCHEMA_VERSION == 11
        # bank_account исчез, карта осталась.
        d.cursor.execute("SELECT id, item_type FROM fin_items ORDER BY id")
        rows = [(r["id"], r["item_type"]) for r in d.cursor.fetchall()]
        assert rows == [(1, "bank_card")]
        # Каскад: связь и галерея bank_account (item_id=2) удалены, карты (1) целы.
        d.cursor.execute("SELECT item_id FROM fin_links ORDER BY item_id")
        assert [r["item_id"] for r in d.cursor.fetchall()] == [1]
        d.cursor.execute("SELECT item_id FROM fin_gallery ORDER BY item_id")
        assert [r["item_id"] for r in d.cursor.fetchall()] == [1]
        # Аккаунт цел.
        d.cursor.execute("SELECT account_name FROM accounts")
        assert d.cursor.fetchone()["account_name"] == "Акк"
    finally:
        d.close(persist=False)


def test_m010_idempotent_without_bank_account(tmp_path):
    """m010 на базе без bank_account удаляет 0 строк и не падает."""
    from hranilka.data.database.migrations.m010_drop_bank_account import migrate as m010
    path = str(tmp_path / "v9.db")
    _make_v9_db(path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON")
        m010(con)
        m010(con)                       # второй прогон — уже нечего удалять
        con.commit()
        n = con.execute("SELECT COUNT(*) AS n FROM fin_items "
                        "WHERE item_type='bank_account'").fetchone()["n"]
        assert n == 0
    finally:
        con.close()


def test_v10_db_migrates_to_v11_drops_ewallet(tmp_path):
    """База v10 с записью ewallet → открытие → v11: запись удалена, её связи и
    галерея подчищены каскадом (FK), карта и кошелёк с их связями/галереями целы."""
    path = str(tmp_path / "v10.db")
    _make_v10_db(path)
    d = _open(path)
    try:
        d.create_tables()
        assert d.get_schema_version() == SCHEMA_VERSION == 11
        # ewallet исчез, карта и криптокошелёк остались.
        d.cursor.execute("SELECT id, item_type FROM fin_items ORDER BY id")
        rows = [(r["id"], r["item_type"]) for r in d.cursor.fetchall()]
        assert rows == [(1, "bank_card"), (2, "crypto_wallet")]
        # Каскад: связь и галерея ewallet (item_id=3) удалены, остальных — целы.
        d.cursor.execute("SELECT item_id FROM fin_links ORDER BY item_id")
        assert [r["item_id"] for r in d.cursor.fetchall()] == [1, 2]
        d.cursor.execute("SELECT item_id FROM fin_gallery ORDER BY item_id")
        assert [r["item_id"] for r in d.cursor.fetchall()] == [1, 2]
        # Аккаунт цел.
        d.cursor.execute("SELECT account_name FROM accounts")
        assert d.cursor.fetchone()["account_name"] == "Акк"
    finally:
        d.close(persist=False)


def test_m011_idempotent_without_ewallet(tmp_path):
    """m011 на базе без ewallet удаляет 0 строк и не падает; карта/кошелёк целы."""
    from hranilka.data.database.migrations.m011_drop_ewallet import migrate as m011
    path = str(tmp_path / "v10.db")
    _make_v10_db(path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON")
        m011(con)
        m011(con)                       # второй прогон — уже нечего удалять
        con.commit()
        n = con.execute("SELECT COUNT(*) AS n FROM fin_items "
                        "WHERE item_type='ewallet'").fetchone()["n"]
        assert n == 0
        n = con.execute("SELECT COUNT(*) AS n FROM fin_items").fetchone()["n"]
        assert n == 2                   # карта и криптокошелёк не тронуты
    finally:
        con.close()


def test_m009_idempotent(tmp_path):
    """Повторный прогон m009 на той же базе не падает (CREATE ... IF NOT EXISTS)."""
    path = str(tmp_path / "v8.db")
    _make_v8_db(path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        m009(con)
        m009(con)                       # второй раз — no-op, без ошибок
        con.commit()
        names = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(_FIN_TABLES) <= names
        # uq_fin_link создан ровно один раз (идемпотентность индекса)
        idx = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert "uq_fin_link" in idx
    finally:
        con.close()


def test_registry_gap_raises(db, monkeypatch):
    """Дырка в реестре (SCHEMA_VERSION поднят, но шаг не зарегистрирован) →
    RuntimeError."""
    monkeypatch.setattr(runner, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
    db.set_schema_version(SCHEMA_VERSION)   # «старая» относительно поднятой версии
    with pytest.raises(RuntimeError, match="Реестр миграций"):
        runner.run(db)


def test_fin_link_unique_and_cascade(db):
    """uq_fin_link запрещает дубль пары; FK CASCADE чистит связи/галерею."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.cursor.execute(
        "INSERT INTO fin_links (item_id, account_id) VALUES (?, ?)", (iid, aid))
    db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.cursor.execute(
            "INSERT INTO fin_links (item_id, account_id) VALUES (?, ?)", (iid, aid))
    db.conn.rollback()                  # откат неудавшейся вставки (связь цела)
    # CASCADE: удаление элемента убирает строки связи и галереи
    db.cursor.execute(
        "INSERT INTO fin_gallery (item_id, description) VALUES (?, 'ф')", (iid,))
    db.conn.commit()
    db.cursor.execute("DELETE FROM fin_items WHERE id = ?", (iid,))
    db.conn.commit()
    db.cursor.execute("SELECT COUNT(*) AS n FROM fin_links WHERE item_id = ?", (iid,))
    assert db.cursor.fetchone()["n"] == 0
    db.cursor.execute("SELECT COUNT(*) AS n FROM fin_gallery WHERE item_id = ?", (iid,))
    assert db.cursor.fetchone()["n"] == 0
