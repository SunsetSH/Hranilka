"""Приветственное окно (обучение): сборка под темами, навигация по слайдам,
логика первого запуска и кнопка повторного показа в настройках."""
import pytest
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent

import config as config_mod
import ui_welcome
from ui_welcome import WelcomeDialog, should_show


@pytest.fixture
def dlg(qapp, pure_config):
    d = WelcomeDialog(pure_config)
    yield d
    d.deleteLater()


def test_builds_under_themes(qapp, pure_config):
    for theme_name in ("Classic IDE", "MS-DOS"):
        colors = config_mod.RETRO_THEMES[theme_name]
        pure_config.set("selected_theme", theme_name)
        pure_config.set("text_color", colors["text"])
        pure_config.set("tree_bg_color", colors["tree_bg"])
        pure_config.set("main_bg_color", colors["main_bg"])
        d = WelcomeDialog(pure_config)
        assert d._stack.count() == 6
        assert d._stack.currentIndex() == 0
        d.deleteLater()


def test_navigation(dlg):
    assert not dlg._back_btn.isEnabled()
    assert dlg._next_btn.text() == "Далее >"

    last = dlg._stack.count() - 1
    for _ in range(last):
        dlg._go(+1)
    assert dlg._stack.currentIndex() == last
    assert dlg._back_btn.isEnabled()
    assert dlg._next_btn.text() == "Начать работу"
    assert dlg._skip_btn.isHidden() or not dlg._skip_btn.isVisibleTo(dlg)

    dlg._go(+1)  # выход за границу кламплен
    assert dlg._stack.currentIndex() == last

    for _ in range(last):
        dlg._go(-1)
    assert dlg._stack.currentIndex() == 0
    assert not dlg._back_btn.isEnabled()
    dlg._go(-1)
    assert dlg._stack.currentIndex() == 0

    # Клавиатурная навигация
    right = QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier)
    dlg.keyPressEvent(right)
    assert dlg._stack.currentIndex() == 1
    left = QKeyEvent(QEvent.KeyPress, Qt.Key_Left, Qt.NoModifier)
    dlg.keyPressEvent(left)
    assert dlg._stack.currentIndex() == 0


def test_next_on_last_slide_accepts(dlg):
    for _ in range(dlg._stack.count() - 1):
        dlg._go(+1)
    accepted = []
    dlg.accepted.connect(lambda: accepted.append(True))
    dlg._on_next()
    assert accepted


def test_should_show(pure_config):
    assert should_show(pure_config) is True
    pure_config.set("welcome_shown", True)
    assert should_show(pure_config) is False


def test_first_run_wiring(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    import main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)

    opened = []

    class _FakeWelcome:
        def __init__(self, config, parent=None):
            opened.append(self)

        def open(self):
            pass

    monkeypatch.setattr(ui_welcome, "WelcomeDialog", _FakeWelcome)

    win = main.MainWindow()
    try:
        win._maybe_show_welcome()
        assert len(opened) == 1
        assert win.config.get("welcome_shown") is True
        win._maybe_show_welcome()  # повторный вызов диалог не создаёт
        assert len(opened) == 1
    finally:
        win.vault.shutdown()
        win._instance_lock.release()


def test_settings_button_exists(qapp, pure_config):
    from dialogs import SettingsDialog
    d = SettingsDialog(pure_config)
    assert d.show_welcome_btn.text() == "Показать обучение"
    d.deleteLater()
