"""m009 — v8→v9: финансовые сущности (карты/кошельки).

Первая боевая нумерованная миграция. Шаг чисто аддитивный и идемпотентный:
создаёт таблицы fin_items/fin_links/fin_gallery и их индексы через
CREATE TABLE/INDEX IF NOT EXISTS. SQL берётся из статик-методов schema.py —
единый источник с create_tables() (новые базы), расхождение исключено.

Выполняется внутри общей транзакции прогона (runner); сам commit не делает.
"""
import sqlite3

from hranilka.data.database.schema import DbSchemaMixin


def migrate(conn: sqlite3.Connection) -> None:
    """Создаёт финансовые таблицы и индексы (идемпотентно, без commit)."""
    DbSchemaMixin._create_fin_tables(conn)
