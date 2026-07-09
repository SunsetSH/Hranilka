"""Доменные исключения слоя данных: по файлу на класс, публичные имена
реэкспортируются здесь."""
from hranilka.data.errors.future_schema import FutureSchemaError
from hranilka.data.errors.premigration_backup import PreMigrationBackupError
from hranilka.data.errors.stale_session import StaleSessionError
from hranilka.data.errors.vault_conflict import VaultConflictError

__all__ = ["FutureSchemaError", "PreMigrationBackupError",
           "StaleSessionError", "VaultConflictError"]
