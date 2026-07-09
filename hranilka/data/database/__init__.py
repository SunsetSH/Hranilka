"""Пакет Database: класс собирается из mixin-модулей (database.py —
CRUD и сборка; concurrency/persistence/schema/state — по зоне
ответственности; migrations/ — эволюция схемы). Публичный API —
здесь."""
from hranilka.data.database.database import (
    Database, SCHEMA_VERSION, FutureSchemaError,
    PreMigrationBackupError, StaleSessionError, VaultConflictError)

__all__ = ["Database", "SCHEMA_VERSION", "FutureSchemaError",
           "PreMigrationBackupError", "StaleSessionError",
           "VaultConflictError"]
