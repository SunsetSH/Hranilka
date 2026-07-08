"""Реестр нумерованных миграций схемы БД (этап 3 реструктуризации).

Как добавить миграцию (пример v8→v9):
  1. Поднять SCHEMA_VERSION в hranilka/data/schema.py до 9.
  2. Создать файл m009_<короткое_имя>.py с функцией
         def migrate(conn: sqlite3.Connection) -> None
     и докстрингом «v8→v9: что меняется и зачем». Шаг выполняется внутри
     общей транзакции прогона (runner), сам commit не делает.
  3. Зарегистрировать ЯВНЫМ импортом ниже:
         from hranilka.data.migrations.m009_short_name import migrate as _m009
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
