"""Базовый каталог приложения. config.json и hranilka.db должны лежать рядом с
программой, а не зависеть от текущего рабочего каталога (откуда её запустили).

Для упакованного exe (PyInstaller) ориентируемся на каталог самого exe; в
обычном запуске — на каталог исходников."""

import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    # Файл живёт в пакете hranilka/ — корень проекта на уровень выше
    # (parents[1]); parent завёл бы вторую БД/конфиг внутри пакета.
    BASE_DIR = Path(__file__).resolve().parents[1]

# Каталог для бандловых ресурсов (assets/), зашитых в exe через PyInstaller
# datas. В onefile-сборке они распаковываются во временный _MEIPASS, а не
# рядом с exe (там лежит только BASE_DIR — конфиг и БД пользователя).
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
