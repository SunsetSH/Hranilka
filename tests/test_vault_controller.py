"""VaultController (Эпик 4.2/4.4): гейт run_exclusive и ожидание воркера.

Требует QApplication (offscreen из conftest). Используем лёгкое фейковое окно
для UI-побочек (строка статуса) — полноценный MainWindow тут не нужен.
"""
import pytest

from PySide6.QtWidgets import QApplication

from database import Database
from vault_controller import VaultController


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


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
