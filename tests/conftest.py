"""Общие фикстуры для тестов Хранилки.

Код программы — пакет hranilka/ в корне проекта; корень добавляется в
sys.path (плюс pythonpath в pytest.ini). Все тесты работают в temp-каталогах
и НЕ трогают рабочие hranilka.db / config.json.
"""
import logging
import os
import sys
import threading

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
    finally:
        # Database() всегда заводит ThreadPoolExecutor(db-worker) в __init__;
        # close() его намеренно не трогает (production переоткрывает
        # соединение при lock/restore). Тестовый экземпляр больше не
        # переоткроется — гасим воркер явно, иначе поток утекает на весь
        # прогон (см. docs/CODE_REVIEW_VPS_SERVERS_2026-07-15.md).
        d.shutdown_executor()


def _dispose_window(win):
    """Полная идемпотентная очистка тестового MainWindow (или его тёзки).

    Порядок продиктован зависимостями:
    1. Таймеры окна — первыми, чтобы _check_idle/_clear_clipboard не тикнули
       посреди остановки vault/db ниже.
    2. vault.shutdown() — прежде db: писатель держит ссылку на db и должен
       перестать её использовать до закрытия соединения.
    3. db.close(persist=False) — закрыть соединение без записи тестовых
       данных на диск, затем db.shutdown_executor() — Database.__init__
       всегда создаёт ThreadPoolExecutor(db-worker); close() его намеренно
       не гасит (production-код переоткрывает соединение при lock/restore),
       поэтому тестовому окну, которое больше не переоткроется, воркер нужно
       остановить явно — иначе поток утекает на весь прогон.
    4. _instance_lock.release() — после db, снятие межпроцессной блокировки
       файла не зависит от состояния соединения.
    5. Сам виджет — БЕЗ win.close(): closeEvent() спрашивает пользователя о
       несохранённых изменениях и конфликте файла модальными диалогами
       (в offscreen-режиме зависнут) и повторно пишет БД на диск. Здесь
       ресурсы уже освобождены вручную (шаги 1-4) — просто прячем и
       планируем удаление C++-объекта.
    """
    for attr in ("_idle_timer", "_clip_timer"):
        timer = getattr(win, attr, None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass

    vault = getattr(win, "vault", None)
    if vault is not None:
        try:
            vault.shutdown()
        except Exception:
            pass

    db = getattr(win, "db", None)
    if db is not None:
        try:
            db.close(persist=False)
        except Exception:
            pass
        try:
            db.shutdown_executor()
        except Exception:
            pass

    lock = getattr(win, "_instance_lock", None)
    if lock is not None:
        try:
            lock.release()
        except Exception:
            pass

    from PySide6.QtWidgets import QApplication
    try:
        win.hide()
    except Exception:
        pass
    win.deleteLater()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


@pytest.fixture
def dispose_window():
    """Фабрика: возвращает dispose_window(win) для teardown window-фикстур
    (см. docstring _dispose_window)."""
    return _dispose_window


@pytest.fixture(scope="session", autouse=True)
def _session_leak_guard():
    """Страж утечек ресурсов теста (не роняет прогон — только сигнал в лог).

    Главный виновник нестабильности test_barrier_flush_persists_inflight_mutation
    (docs/РАЗБОР_НЕСТАБИЛЬНЫЙ_VAULT_ТЕСТ.md) — накопление живых потоков
    db-worker* (по одному ThreadPoolExecutor на каждый несдвинутый Database) и
    таймеров/окон между тестами. Проверяем это явно по имени потока, а не
    только по общему порогу — так причина видна сразу в логе."""
    yield
    threads = threading.enumerate()
    db_workers = [t for t in threads if t.name.startswith("db-worker")]
    if db_workers:
        logging.warning(
            "Утечка потоков БД к концу прогона: %d db-worker* (%s) — "
            "проверь shutdown_executor() в соответствующих фикстурах.",
            len(db_workers), ", ".join(t.name for t in db_workers))
    if len(threads) > 10:
        logging.warning(
            "Много живых потоков к концу прогона: %d (порог 10) — "
            "возможна утечка ресурсов теста.", len(threads))


@pytest.fixture
def pure_config(tmp_path, monkeypatch):
    """Config с дефолтами (load() не находит файла → чистые значения).

    Указываем config.CONFIG_FILE на несуществующий путь, чтобы тесты не зависели
    от config.json на машине и не перезаписывали его."""
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    return config.Config()
