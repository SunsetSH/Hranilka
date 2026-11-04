"""B10: запуск прерывается StartupAborted (SystemExit), create() -> None,
блокировка экземпляра освобождается."""
import pytest

from hranilka.ui.db_lifecycle import StartupAborted


def _setup(tmp_path, monkeypatch):
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    return main


def test_startup_aborted_is_systemexit():
    e = StartupAborted(1, "x")
    assert isinstance(e, SystemExit) and e.code == 1 and e.reason == "x"


def test_create_returns_none_when_locked(qapp, tmp_path, monkeypatch, dispose_window):
    main = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main.theme, "themed_info", lambda *a, **k: None)
    first = main.MainWindow.create()
    try:
        assert first is not None
        with pytest.raises(SystemExit):
            main.MainWindow()
        assert main.MainWindow.create() is None
        assert main.MainWindow.last_exit_code == 0
    finally:
        dispose_window(first)


def test_lock_released_after_unlock_cancel(qapp, tmp_path, monkeypatch, dispose_window):
    main = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main.MainWindow, "_open_database", lambda self, parent=None: False)
    assert main.MainWindow.create() is None
    monkeypatch.undo()
    main = _setup(tmp_path, monkeypatch)
    win = main.MainWindow.create()            # lock свободен -> окно создаётся
    try:
        assert win is not None
    finally:
        dispose_window(win)
