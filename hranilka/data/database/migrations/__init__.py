"""Реестр нумерованных миграций схемы БД (этап 3 реструктуризации).

Как добавить миграцию (пример v8→v9):
  1. Поднять SCHEMA_VERSION в hranilka/data/database/schema.py до 9.
  2. Создать файл m009_<короткое_имя>.py с функцией
         def migrate(conn: sqlite3.Connection) -> None
     и докстрингом «v8→v9: что меняется и зачем». Шаг выполняется внутри
     общей транзакции прогона (runner), сам commit не делает.
  3. Зарегистрировать ЯВНЫМ импортом ниже:
         from hranilka.data.database.migrations.m009_short_name import migrate as _m009
         MIGRATIONS[9] = _m009
     Динамическое сканирование (pkgutil/importlib) НЕ применять — PyInstaller
     onefile не увидит такие модули без hiddenimports.

БД версий ≤ LEGACY_BASE (8) догоняются декларативным diff-ом колонок и
идемпотентными починками (см. runner.run) — ретро-шаги m001–m008 не нужны.
"""
import sqlite3
from typing import Callable

# Версия, до которой (включительно) старые БД догоняет legacy-механизм
# (декларативный diff колонок в runner.run).
LEGACY_BASE = 8

MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}


def drop_fin_item_type(conn: sqlite3.Connection, item_type: str) -> None:
    """Удаляет записи fin_items указанного типа, убранного из реестра
    дескрипторов (см. m010/m011). Каскад (FK ON DELETE CASCADE) чистит
    fin_links/fin_gallery. Идемпотентно — повторный прогон удалит 0 строк.
    Общий шаг для миграций «тип убран из реестра»: без него каждое такое
    удаление типа копипастило бы один и тот же DELETE в новый модуль."""
    conn.execute("DELETE FROM fin_items WHERE item_type = ?", (item_type,))

# v8→v9: финансовые сущности. Регистрация ЯВНЫМ импортом (не pkgutil — иначе
# PyInstaller onefile не увидит модуль без hiddenimports).
from hranilka.data.database.migrations.m009_fin_items import migrate as _m009

MIGRATIONS[9] = _m009

# v9→v10: удаление типа «Банковский счёт» (bank_account) из fin_items.
from hranilka.data.database.migrations.m010_drop_bank_account import migrate as _m010

MIGRATIONS[10] = _m010

# v10→v11: удаление типа «Электронный кошелёк» (ewallet) из fin_items.
from hranilka.data.database.migrations.m011_drop_ewallet import migrate as _m011

MIGRATIONS[11] = _m011

# v11→v12: VPS-серверы — таблицы servers/server_links/server_gallery.
from hranilka.data.database.migrations.m012_servers import migrate as _m012

MIGRATIONS[12] = _m012
