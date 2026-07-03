"""VaultController (Эпик 4.2/4.4): гейт run_exclusive и ожидание воркера.

Требует QApplication (offscreen из conftest). Используем лёгкое фейковое окно
для UI-побочек (строка статуса) — полноценный MainWindow тут не нужен.
"""
import pytest

from database import Database
from vault_controller import VaultController

# Общий session-qapp живёт в conftest.py (M-16).


class _FakeBar:
    def showMessage(self, *a, **k):
        pass


class _FakeWindow:
    def statusBar(self):
        return _FakeBar()


@pytest.fixture
def vc(qapp, tmp_db_path, pure_config):
    db = Database(tmp_db_path)
    db.connect()
    db.create_tables()
    controller = VaultController(db, _FakeWindow(), pure_config)
    yield controller
    controller.shutdown()
    db.close(persist=False)


def test_wait_idle_true_when_not_busy(vc):
    assert vc.wait_idle() is True


def test_run_exclusive_returns_result(vc):
    ok, res = vc.run_exclusive(lambda: 7)
    assert ok is True and res == 7


def test_run_exclusive_reports_error(vc):
    def boom():
        raise RuntimeError("сбой операции")
    ok, res = vc.run_exclusive(boom)
    assert ok is False and "сбой операции" in res


def test_vault_locked_during_op(vc):
    # Внутри привилегированной операции монопольный флаг взведён; после — снят.
    seen = {}

    def op():
        seen["locked"] = vc._vault_locked
        return None
    vc.run_exclusive(op)
    assert seen["locked"] is True
    assert vc._vault_locked is False


def test_plaintext_flush_is_noop(vc):
    # В обычном режиме flush не падает и ничего не «залипает».
    vc.flush()
    assert vc._write_busy is False


# ─── H7-01: restore не теряет последние изменения (encrypted) ────────────────

def test_barrier_flush_persists_inflight_mutation(qapp, tmp_db_path, pure_config):
    """Последовательность restore/close (барьер executor'а → flush → wait_idle)
    сохраняет на диск даже коммит, выполнявшийся в фоновом потоке в момент старта:
    после неё расшифрованный контейнер содержит правку."""
    import threading

    import crypto_store as cs
    from database import Database

    db = Database(tmp_db_path)
    db.connect()
    db.create_tables()
    recovery = db.enable_encryption("pw", "fast")   # → шифр. режим, dirty сброшен
    assert recovery
    vc = VaultController(db, _FakeWindow(), pure_config)
    try:
        started = threading.Event()
        release = threading.Event()

        def slow_mutation():
            started.set()
            release.wait(2)
            db.add_service("inflight")          # коммит из фонового потока (dirty=True)

        fut = db._executor.submit(slow_mutation)
        assert started.wait(2)
        release.set()

        # Порядок как в _restore_from_backup/closeEvent: барьер → flush → wait_idle.
        assert db.wait_executor_idle(timeout=5) is True
        vc.flush()
        assert vc.wait_idle() is True
        fut.result(timeout=2)
    finally:
        vc.shutdown()

    # На диске — контейнер с правкой (расшифровываем и проверяем сервис на месте).
    with open(tmp_db_path, "rb") as f:
        container = f.read()
    db_bytes, dek, header = cs.unlock(container, "pw")
    d2 = Database(tmp_db_path)
    d2.open_encrypted(db_bytes, dek, header)
    d2.create_tables()
    assert "inflight" in [s["name"] for s in d2.get_services()]
    d2.close(persist=False)
    db.close(persist=False)
