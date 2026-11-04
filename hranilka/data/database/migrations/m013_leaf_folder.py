"""m013 — v12→v13: лист (аккаунт/карта/кошелёк/сервер) прямо в папке.

Добавляет колонку folder_id (INTEGER NULL, FK folders ON DELETE SET NULL,
CHECK «максимум один из service_id/folder_id») в accounts, fin_items, servers
и индексы idx_<table>_folder. Существующие строки получают NULL — иерархия
не меняется. Идемпотентно: наличие колонки проверяется через PRAGMA, индексы
создаются IF NOT EXISTS; общий код с create_tables (schema.py).

Выполняется внутри общей транзакции прогона (runner); сам commit не делает.
"""
import sqlite3

from hranilka.data.database.schema import DbSchemaMixin


def migrate(conn: sqlite3.Connection) -> None:
    """Добавляет folder_id и индексы листьям дерева (идемпотентно, без commit)."""
    DbSchemaMixin._ensure_leaf_folder_columns(conn)
