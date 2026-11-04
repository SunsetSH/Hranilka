"""Конвейер миграции схемы БД (этап 3 реструктуризации).

Порядок повторяет прежний Database._migrate и дополняется нумерованными
шагами из реестра MIGRATIONS:

  1. Версия актуальна — выход; версия новее поддерживаемой — FutureSchemaError.
  2. Legacy-база (≤ LEGACY_BASE): декларативный diff колонок
     (_EXPECTED_COLUMNS → ALTER TABLE ADD COLUMN) + идемпотентные починки
     (nullable service_id, битые ссылки на accounts_old). Проверен годами и
     догоняет БД ЛЮБОЙ старой версии до v8 — ретро-шаги не выдумываем.
  3. Нумерованные шаги LEGACY_BASE+1..SCHEMA_VERSION по порядку.
  4. Шаги п.3 + set_schema_version — одна явная транзакция (BEGIN/COMMIT,
     откат при сбое). Legacy-перестройки п.2 (PRAGMA foreign_keys) — вне её.

Fast-path по отпечатку схемы и pre-migration durable-копии живут в
schema.py (create_tables) — вызываются ДО этого конвейера, как и раньше.
"""
from typing import TYPE_CHECKING

from hranilka.data.errors import FutureSchemaError
from hranilka.data.database.migrations import LEGACY_BASE, MIGRATIONS
from hranilka.data.database.schema import SCHEMA_VERSION

if TYPE_CHECKING:
    from hranilka.data.database import Database


def _check_registry() -> None:
    """Реестр обязан покрывать ровно версии LEGACY_BASE+1..SCHEMA_VERSION.

    Ловит на старте разработки две ошибки: подняли SCHEMA_VERSION, но забыли
    зарегистрировать шаг, — и наоборот, зарегистрировали шаг вне диапазона."""
    expected = set(range(LEGACY_BASE + 1, SCHEMA_VERSION + 1))
    if set(MIGRATIONS) != expected:
        raise RuntimeError(
            f"Реестр миграций не совпадает со SCHEMA_VERSION: "
            f"зарегистрированы {sorted(MIGRATIONS)}, ожидались {sorted(expected)}. "
            f"См. hranilka/data/database/migrations/__init__.py.")


def run(db: "Database") -> None:
    """Привести открытую БД к актуальной версии схемы (см. докстринг модуля)."""
    current = db.get_schema_version()
    if current == SCHEMA_VERSION:
        return
    if current > SCHEMA_VERSION:
        # База создана более новой версией программы. Молчаливая «миграция»
        # вниз записала бы устаревшую версию схемы и могла бы необратимо
        # повредить данные — поэтому отказываемся открывать.
        raise FutureSchemaError(current, SCHEMA_VERSION)
    _check_registry()

    # Legacy: догоняем старые базы до LEGACY_BASE декларативным diff-ом.
    # Идемпотентно — безопасно выполняется и на базах промежуточных версий.
    db._add_missing_columns()
    db._migrate_accounts_service_nullable()
    # Чинит базы, испорченные старой (ошибочной) версией миграции,
    # где ссылки внешних ключей указывали на несуществующую accounts_old.
    db._repair_accounts_old_refs()

    # Нумерованные шаги: строго по порядку от max(current, LEGACY_BASE)+1.
    # Шаги m009+ содержат только DDL/DML без PRAGMA (перестройки с
    # foreign_keys=OFF живут выше, в legacy-блоке, и идут вне транзакции), а
    # SQLite DDL транзакционен — поэтому все шаги и запись версии выполняются
    # ОДНОЙ явной транзакцией: сбой любого шага откатывает всё, версия не
    # поднимается, повторный запуск начнёт с тех же шагов.
    conn = db.conn
    conn.commit()                       # закрыть возможную неявную транзакцию
    conn.execute("BEGIN")
    try:
        for version in range(max(current, LEGACY_BASE) + 1, SCHEMA_VERSION + 1):
            MIGRATIONS[version](conn)
        db.set_schema_version(SCHEMA_VERSION)
    except BaseException:
        conn.rollback()
        raise
    db._commit()
