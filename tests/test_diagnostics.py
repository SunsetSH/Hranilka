"""Диагностика запуска: этапы в логе и причина прерывания."""
import logging

from hranilka import app
from hranilka.core import diagnostics
from hranilka.ui.db_lifecycle import StartupAborted


def test_startup_stage_logged(caplog):
    with caplog.at_level(logging.INFO):
        diagnostics.startup_stage("проверка")
    diagnostics.pause_watchdog()
    assert "проверка" in caplog.text


def test_main_logs_startup_abort_reason(caplog, monkeypatch):
    def aborted():
        raise StartupAborted(0, "unlock cancelled")

    monkeypatch.setattr(app, "_run", aborted)
    monkeypatch.setattr(app, "setup_logging", lambda: None)
    monkeypatch.setattr(app, "install_excepthooks", lambda: None)
    monkeypatch.setattr(diagnostics, "enable_fault_log", lambda _d: None)
    monkeypatch.setattr(diagnostics, "install_hooks", lambda: None)
    monkeypatch.setattr(app, "qInstallMessageHandler", lambda _h: None)
    with caplog.at_level(logging.INFO):
        assert app.main() == 0
    assert "unlock cancelled" in caplog.text


def test_fault_log_created(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "_fault_file", None)
    diagnostics.enable_fault_log(tmp_path)
    assert (tmp_path / diagnostics.CRASH_LOG_NAME).exists()
    diagnostics.faulthandler.disable()
    diagnostics._fault_file.close()
