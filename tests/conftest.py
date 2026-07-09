"""Общие фикстуры для тестов Хранилки.

Код программы — пакет hranilka/ в корне проекта; корень добавляется в
sys.path (плюс pythonpath в pytest.ini). Все тесты работают в temp-каталогах
и НЕ трогают рабочие hranilka.db / config.json.
"""
import os
import sys

import pytest

# Qt-тесты (галерея и т.п.) должны работать без дисплея — headless.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Корень проекта = родитель папки tests/
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@pytest.fixture(scope="session")
def qapp():
    """Единственный QApplication на весь прогон (M-16).

    Qt допускает не более одного экземпляра QApplication на процесс. Раньше
    каждый модуль (галерея, окно, контроллер) заводил свою per-module фикстуру —
    все они возвращали один и тот же процессный экземпляр, но фикстуры дублировались.
    Здесь фикстура одна и переиспользуется всеми тестами. Модульные вызовы
    QImageReader.setAllocationLimit(...) остаются в своих файлах — эффект
    процессно-глобальный, поэтому важен порядок, а не место фикстуры."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def tmp_db_path(tmp_path):
    """Путь к несуществующей ещё БД во временном каталоге."""
    return str(tmp_path / "hranilka.db")


@pytest.fixture
def db(tmp_db_path):
    """Открытая обычная (незашифрованная) БД Хранилки со всеми таблицами."""
    from hranilka.data.database import Database
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    yield d
    try:
        d.close(persist=False)
    except Exception:
        pass


@pytest.fixture
def pure_config(tmp_path, monkeypatch):
    """Config с дефолтами (load() не находит файла → чистые значения).

    Указываем config.CONFIG_FILE на несуществующий путь, чтобы тесты не зависели
    от config.json на машине и не перезаписывали его."""
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    return config.Config()
