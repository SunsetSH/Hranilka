"""m010 — v9→v10: удаление типа «Банковский счёт» (bank_account).

Тип bank_account убран из реестра дескрипторов, поэтому его записи в БД больше
некому открыть/отрисовать. Шаг удаляет их из fin_items; связанные строки
fin_links/fin_gallery снимаются каскадом (FK ON DELETE CASCADE, foreign_keys=ON
включён при подключении — см. persistence.connect).

Идемпотентно (повторный прогон на базе без bank_account удалит 0 строк) и
выполняется внутри общей транзакции прогона (runner) — сам commit не делает.
"""
import sqlite3

from hranilka.data.database.migrations import drop_fin_item_type


def migrate(conn: sqlite3.Connection) -> None:
    """Удаляет записи типа bank_account (каскад чистит связи и галерею)."""
    drop_fin_item_type(conn, "bank_account")
