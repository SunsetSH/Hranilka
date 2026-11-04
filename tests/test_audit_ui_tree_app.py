"""Аудит B10: логирование, excepthook, код возврата app.main."""
import logging
import sys
from logging.handlers import RotatingFileHandler

import pytest

from hranilka import app


@pytest.fixture
def clean_logging():
    root = logging.getLogger()
    before = list(root.handlers)
    hook, level = sys.excepthook, root.level
    yield
    for h in list(root.handlers):
        if h not in before:
            root.removeHandler(h)
            h.close()
    sys.excepthook = hook
    root.setLevel(level)


def test_setup_logging_creates_rotating_file(tmp_path, clean_logging):
    app.setup_logging(tmp_path)
    logging.getLogger("t").error("hello")
    handlers = [h for h in logging.getLogger().handlers
                if isinstance(h, RotatingFileHandler)]
    assert handlers
    handlers[-1].flush()
    assert "hello" in (tmp_path / app.LOG_FILE_NAME).read_text(encoding="utf-8")


def test_uncaught_logged_and_dialog(qapp, monkeypatch, clean_logging, caplog):
    shown = []
    monkeypatch.setattr(app.QMessageBox, "critical",
                        lambda *a, **k: shown.append(a))
    try:
        raise ValueError("boom")
    except ValueError:
        app._report_uncaught(*sys.exc_info())
    assert shown and "boom" in shown[0][2]
    assert "boom" in caplog.text


def test_main_returns_exit_code_on_systemexit(monkeypatch, tmp_path, clean_logging):
    monkeypatch.setattr(app, "BASE_DIR", tmp_path)

    def run0():
        raise SystemExit(0)

    def run3():
        raise SystemExit(3)

    monkeypatch.setattr(app, "_run", run0)
    assert app.main() == 0
    monkeypatch.setattr(app, "_run", run3)
    assert app.main() == 3
