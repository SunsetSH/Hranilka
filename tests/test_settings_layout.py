"""Окно настроек: вкладка звуков и пределы высоты."""
from hranilka.core.config import Config
from hranilka.ui.dialogs.settings.dialog import SettingsDialog


def test_sounds_live_on_their_own_tab(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("hranilka.core.config.CONFIG_FILE", tmp_path / "c.json")
    dialog = SettingsDialog(Config())
    titles = [b.text() for b in dialog._tabs.tab_buttons()]
    assert "Звуки" in titles
    sounds_page = dialog._tabs._stack.widget(titles.index("Звуки")).widget()
    assert sounds_page.isAncestorOf(dialog.sound_volume_slider)
    behavior = dialog._tabs._stack.widget(titles.index("Поведение")).widget()
    assert not behavior.isAncestorOf(dialog.sound_volume_slider)


def test_dialog_fits_screen_even_with_large_font(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("hranilka.core.config.CONFIG_FILE", tmp_path / "c.json")
    config = Config()
    config.set("font_size", 24)
    dialog = SettingsDialog(config)
    dialog.show()
    qapp.processEvents()
    dialog._relayout_after_show()
    screen = dialog.screen().availableGeometry()
    assert dialog.height() <= screen.height()
    assert dialog.width() <= screen.width()
    assert dialog.frameGeometry().bottom() <= screen.bottom()
    dialog.close()
