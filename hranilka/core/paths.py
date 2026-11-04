"""Базовый каталог приложения. config.json и hranilka.db должны лежать рядом с
программой, а не зависеть от текущего рабочего каталога (откуда её запустили).

Для упакованного exe (PyInstaller) ориентируемся на каталог самого exe; в
обычном запуске — на каталог исходников."""

import os
import sys
import tempfile
from pathlib import Path


def _is_writable(folder: Path) -> bool:
    """Можно ли создавать файлы в каталоге (реальная проба, а не os.access:
    на Windows он не учитывает ACL/read-only тома)."""
    try:
        with tempfile.TemporaryFile(dir=folder):
            return True
    except OSError:
        return False


def resolve_base_dir(candidate: Path, appdata: str | None = None) -> Path:
    """Каталог данных: candidate, если он доступен на запись (portable/dev —
    поведение прежнее); иначе %APPDATA%\\Hranilka (например, установка в
    Program Files). Нет APPDATA или не создаётся — остаётся candidate."""
    if _is_writable(candidate):
        return candidate
    root = appdata if appdata is not None else os.environ.get("APPDATA")
    if root:
        fallback = Path(root) / "Hranilka"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
        except OSError:
            return candidate
        if _is_writable(fallback):
            return fallback
    return candidate


if getattr(sys, "frozen", False):
    _APP_DIR = Path(sys.executable).resolve().parent
else:
    # Файл живёт в hranilka/core/ — корень проекта на ДВА уровня выше
    # (parents[2]); меньший индекс завёл бы вторую БД/конфиг внутри пакета.
    # При переносе файла глубже/выше — пересчитать индекс!
    _APP_DIR = Path(__file__).resolve().parents[2]
BASE_DIR = resolve_base_dir(_APP_DIR)

# Каталог для бандловых ресурсов (assets/), зашитых в exe через PyInstaller
# datas. В onefile-сборке они распаковываются во временный _MEIPASS, а не
# рядом с exe (там лежит только BASE_DIR — конфиг и БД пользователя).
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", _APP_DIR))
