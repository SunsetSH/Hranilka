"""m012 — v11→v12: VPS-серверы.

Шаг чисто аддитивный и идемпотентный: создаёт таблицы servers/server_links/
server_gallery и их индексы через CREATE TABLE/INDEX IF NOT EXISTS. SQL
берётся из статик-методов schema.py — единый источник с create_tables()
(новые базы), расхождение исключено.

Выполняется внутри общей транзакции прогона (runner); сам commit не делает.
"""
import sqlite3

from hranilka.data.database.schema import DbSchemaMixin


def migrate(conn: sqlite3.Connection) -> None:
    """Создаёт таблицы серверов и индексы (идемпотентно, без commit)."""
    DbSchemaMixin._create_server_tables(conn)
