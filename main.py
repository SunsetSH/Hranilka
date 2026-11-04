"""Точка входа «Хранилки»: python main.py (и entry PyInstaller).
Вся сборка приложения — в hranilka/app.py, окно — в hranilka/ui/main_window.py."""
import sys

from hranilka.app import main

if __name__ == "__main__":
    sys.exit(main())
