"""Смоук TitleBar (offscreen): сборка, кнопки, переключение «развернуть».
Тесты поведенческие — не завязаны на вёрстку/размеры."""
import pytest
from PySide6.QtWidgets import QWidget

from hranilka.ui.titlebar import TitleBar


@pytest.fixture
def bar(qapp):
    host = QWidget()
    tb = TitleBar(host)
    yield tb
    tb.deleteLater()
    host.deleteLater()


def test_buttons_exist(bar):
    for name in ("min_btn", "max_btn", "close_btn", "settings_btn", "bin_btn"):
        assert hasattr(bar, name)


def test_buttons_emit_signals(bar):
    got = []
    bar.minimize_requested.connect(lambda: got.append("min"))
    bar.maximize_requested.connect(lambda: got.append("max"))
    bar.close_requested.connect(lambda: got.append("close"))
    bar.settings_requested.connect(lambda: got.append("settings"))
    bar.min_btn.click()
    bar.max_btn.click()
    bar.close_btn.click()
    bar.settings_btn.click()
    assert got == ["min", "max", "close", "settings"]


def test_maximize_toggle_changes_icon_and_back(bar):
    normal = bar.max_btn.text()
    bar.set_maximized(True)
    maximized = bar.max_btn.text()
    assert maximized != normal
    bar.set_maximized(False)
    assert bar.max_btn.text() == normal


def test_bin_button_visibility_follows_count(bar):
    bar.update_bin(0)
    assert bar.bin_btn.isHidden()
    bar.update_bin(3)
    assert not bar.bin_btn.isHidden()
    assert "3" in bar.bin_btn.text()
    bar.update_bin(0)
    assert bar.bin_btn.isHidden()
