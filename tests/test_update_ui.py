"""Обновления в UI: окно релиза, контроллер, настройки, обучение, связанные правки."""
import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QLabel, QWidget

from hranilka.services import update_install, updater
from hranilka.services.updater import ReleaseInfo, StagedUpdate, UpdateError
from hranilka.ui import update_controller as uc
from hranilka.ui.dialogs import update_dialog as ud

RELEASE = ReleaseInfo("9.0", "<b>Новое</b>", updater.RELEASES_URL + "/tag/9.0",
                      "https://github.com/x", 5 * 1024 * 1024, "https://github.com/y")


class Cfg:
    def __init__(self, **kw):
        self.d = {"check_updates": False, **kw}

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value):
        self.d[key] = value

    def save(self):
        pass


def _pump(until, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not until():
        QCoreApplication.processEvents()
        time.sleep(0.002)
    assert until()


@pytest.fixture
def dialog(qapp):
    d = ud.UpdateDialog(Cfg(), RELEASE)
    yield d
    d._cancel.set()
    d.close()


def test_dialog_shows_notes_as_plain_text(dialog):
    from PySide6.QtWidgets import QPlainTextEdit
    notes = dialog.findChild(QPlainTextEdit)
    assert notes.toPlainText() == RELEASE.notes and notes.isReadOnly()


def test_dialog_has_manual_link_and_auto_button(dialog):
    assert RELEASE.page_url in dialog.link_label.text()
    assert dialog.update_btn.text() == "Обновить автоматически"
    assert dialog.update_btn.isEnabled()


def test_dialog_link_opens_verified_url_not_markup(dialog, monkeypatch):
    opened = []
    monkeypatch.setattr(ud.QDesktopServices, "openUrl", lambda url: opened.append(url))
    dialog._open_release_page("https://evil.com")
    assert [u.toString() for u in opened] == [RELEASE.page_url]


def test_dialog_blocker_disables_auto(qapp):
    d = ud.UpdateDialog(Cfg(), RELEASE, "нет прав")
    assert not d.update_btn.isEnabled() and "нет прав" in d.status.text()


def test_dialog_failure_keeps_window_and_allows_retry(dialog, monkeypatch):
    def fail(*_a):
        raise UpdateError("подпись не сошлась")
    monkeypatch.setattr(ud.updater, "prepare", fail)
    dialog._start()
    _pump(lambda: "подпись не сошлась" in dialog.status.text())
    assert dialog.update_btn.isEnabled() and not dialog.bar.isVisible()


def test_dialog_unexpected_exception_reported(dialog, monkeypatch):
    def fail(*_a):
        raise RuntimeError("секретная деталь")
    monkeypatch.setattr(ud.updater, "prepare", fail)
    dialog._start()
    _pump(lambda: "Непредвиденная" in dialog.status.text())
    assert "секретная" not in dialog.status.text()


def test_dialog_success_emits_install_ready(dialog, monkeypatch, tmp_path):
    staged = StagedUpdate(tmp_path / "x", "0", "9.0")

    def prepare(release, progress, cancelled):
        progress(5, 10)
        return staged
    monkeypatch.setattr(ud.updater, "prepare", prepare)
    got = []
    dialog.install_ready.connect(got.append)
    dialog._start()
    _pump(lambda: bool(got))
    assert got == [staged]


def test_dialog_cancel_discards_late_result(dialog, monkeypatch, tmp_path):
    path = tmp_path / "x"
    path.write_bytes(b"1")
    got = []
    dialog.install_ready.connect(got.append)
    dialog.reject()
    dialog._on_done(StagedUpdate(path, "0", "9.0"))
    assert got == [] and not path.exists()


class Window(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.config = cfg
        self.close_result = True
        self.messages = []

    def close(self):
        return self.close_result

    def statusBar(self):
        return type("Bar", (), {
            "showMessage": lambda _s, text, ms: self.messages.append(text)})()


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(update_install, "_pending", None)
    monkeypatch.setattr(update_install, "_startup_state", None)
    infos = []
    monkeypatch.setattr(uc.theme, "themed_info",
                        lambda cfg, parent, title, text, icon=None:
                        infos.append((title, text)))
    w = Window(Cfg())
    w.infos = infos
    w.controller = uc.UpdateController(w)
    yield w
    w.deleteLater()


def test_startup_without_consent_never_touches_network(window, monkeypatch):
    monkeypatch.setattr(uc.updater, "fetch_latest",
                        lambda: pytest.fail("сеть без согласия"))
    window.controller.on_startup()
    assert not window.controller._checking


def test_startup_check_shows_dialog(window, monkeypatch):
    window.config.set("check_updates", True)
    monkeypatch.setattr(uc.updater, "fetch_latest", lambda: RELEASE)
    window.show()
    window.controller.on_startup()
    _pump(lambda: window.controller._dialog is not None)
    window.controller._dialog.reject()


def test_startup_network_error_is_silent(window, monkeypatch):
    window.config.set("check_updates", True)

    def fail():
        raise UpdateError("нет сети")
    monkeypatch.setattr(uc.updater, "fetch_latest", fail)
    window.controller.on_startup()
    _pump(lambda: not window.controller._checking)
    assert window.infos == [] and window.controller._dialog is None


def test_startup_result_waits_for_active_modal(window, monkeypatch):
    other = QWidget()
    monkeypatch.setattr(uc.QApplication, "activeModalWidget", lambda: other)
    window.show()
    window.controller._present(RELEASE, window)
    assert window.controller._dialog is None


def test_manual_check_reports_up_to_date(window, monkeypatch):
    monkeypatch.setattr(uc.updater, "fetch_latest", lambda: None)
    done = []
    window.show()
    window.controller.check_now(window, lambda: done.append(1))
    _pump(lambda: done == [1])
    assert window.infos == [("Обновления", "Установлена последняя версия.")]


def test_manual_check_reports_error(window, monkeypatch):
    def fail():
        raise UpdateError("нет сети")
    monkeypatch.setattr(uc.updater, "fetch_latest", fail)
    done = []
    window.show()
    window.controller.check_now(window, lambda: done.append(1))
    _pump(lambda: done == [1])
    assert window.infos[0][1] == "нет сети"


def test_manual_check_during_startup_check_gets_result(window, monkeypatch):
    done = []
    window.show()
    window.controller._checking = True            # идёт проверка при запуске
    window.controller.check_now(window, lambda: done.append(1))
    assert done == []
    window.controller._on_checked(None, None)
    assert done == [1] and len(window.infos) == 1


def test_new_dialog_waits_for_cancelled_download(window, monkeypatch):
    monkeypatch.setattr(uc.QApplication, "activeModalWidget", lambda: None)
    previous = type("D", (), {"worker_alive": lambda _s: True})()
    window.controller._dialog = previous
    window.show()
    window.controller._present(RELEASE, window)
    assert window.controller._dialog is previous


def test_install_refused_close_cancels_update(window, monkeypatch, tmp_path):
    path = tmp_path / "x"
    path.write_bytes(b"1")
    window.close_result = False
    window.controller._begin_install(StagedUpdate(path, "0", "9.0"))
    _pump(lambda: bool(window.infos))
    assert window.infos[0][0] == "Обновление отменено"
    assert update_install._pending is None and not path.exists()


def test_install_closes_window_and_quits(window, monkeypatch, tmp_path):
    quits = []
    monkeypatch.setattr(uc.QApplication, "quit", lambda: quits.append(1))
    staged = StagedUpdate(tmp_path / "x", "0", "9.0")
    window.controller._begin_install(staged)
    _pump(lambda: bool(quits))
    assert update_install._pending == staged


def test_startup_failed_state_is_reported(window, monkeypatch):
    monkeypatch.setattr(update_install, "_startup_state", "failed")
    window.controller.on_startup()
    assert window.infos[0][0] == "Обновление не выполнено"


def test_startup_updated_state_goes_to_status_bar(window, monkeypatch):
    monkeypatch.setattr(update_install, "_startup_state", "updated")
    window.controller.on_startup()
    assert window.messages == ["Программа обновлена."] and window.infos == []


@pytest.fixture
def settings(qapp, tmp_path, monkeypatch):
    from hranilka.core import config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui.dialogs.settings.dialog import SettingsDialog
    d = SettingsDialog(cfgmod.Config())
    yield d
    d.close()


def test_defaults_updates_off_and_volume_40(settings):
    assert settings.config.get("check_updates") is False
    assert settings.check_updates_check.isChecked() is False
    assert settings.config.get("sound_volume") == 40
    assert settings.sound_volume_slider.value() == 40


def test_sound_toggle_label(settings):
    assert settings.sounds_enabled_check.text() == "Включить звуки"


def test_updates_checkbox_saved_on_apply(settings):
    settings.check_updates_check.setChecked(True)
    assert settings._has_unsaved_changes()
    settings._apply_settings()
    assert settings.config.get("check_updates") is True


def test_check_now_button_blocked_until_done(settings):
    done = []
    settings.set_update_checker(lambda parent, on_done: done.append(on_done))
    settings.check_updates_btn.click()
    assert not settings.check_updates_btn.isEnabled()
    done[0]()
    assert settings.check_updates_btn.isEnabled()


@pytest.mark.parametrize("change", [
    lambda d: d.sound_volume_slider.setValue(11),
    lambda d: d.sounds_enabled_check.toggle(),
    lambda d: d.sound_event_checks["copy"].toggle(),
    lambda d: d.image_downscale_check.toggle(),
])
def test_all_controls_tracked_as_unsaved(settings, change):
    assert not settings._has_unsaved_changes()
    change(settings)
    assert settings._has_unsaved_changes()


def test_theme_preview_hidden_without_gap(settings):
    assert not hasattr(settings, "theme_preview")
    from PySide6.QtWidgets import QGroupBox
    assert "Предпросмотр" not in {g.title() for g in settings.findChildren(QGroupBox)}


@pytest.mark.parametrize("font,size", [("Cascadia Code", 14), ("Courier New", 16),
                                       ("Courier New", 24)])
def test_crt_checkbox_text_not_clipped(qapp, tmp_path, monkeypatch, font, size):
    from hranilka.core import config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui.dialogs.settings.dialog import SettingsDialog
    cfg = cfgmod.Config()
    cfg.set("font", font)
    cfg.set("font_size", size)
    d = SettingsDialog(cfg)
    d.show()
    QCoreApplication.processEvents()
    try:
        assert d.crt_mode_check.width() >= d.crt_mode_check.sizeHint().width()
    finally:
        d.close()


def test_color_picker_applies_live(settings, monkeypatch):
    from PySide6.QtGui import QColor
    from hranilka.ui.dialogs.settings import dialog as dlgmod
    monkeypatch.setattr(dlgmod.QColorDialog, "getColor",
                        lambda *a: QColor("#123456"))
    fired = []
    settings.appearance_changed.connect(lambda: fired.append(1))
    settings._choose_color("text_color")
    assert fired == [1] and settings.config.get("text_color") == "#123456"


class Host(QWidget):
    """Главное окно для повторного обучения из настроек."""
    def __init__(self, result=True):
        super().__init__()
        self.result = result
        self.calls = []

    def _apply_welcome_fin_instruments(self, show):
        self.calls.append(("fin", show))
        return self.result

    def _apply_welcome_recycle_bin(self, enabled):
        self.calls.append(("bin", enabled))
        return self.result

    def _apply_welcome_servers(self, show):
        self.calls.append(("srv", show))
        return self.result


@pytest.fixture
def child_settings(qapp, tmp_path, monkeypatch):
    from hranilka.core import config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui.dialogs.settings.dialog import SettingsDialog
    host = Host()
    d = SettingsDialog(cfgmod.Config(), host)
    yield d, host
    d.close()


def test_welcome_choices_reach_main_window_without_recursion(child_settings):
    d, host = child_settings
    assert d._apply_welcome_fin_instruments(True) is True
    assert d._apply_welcome_recycle_bin(True) is True
    assert d._apply_welcome_servers(True) is True
    assert host.calls == [("fin", True), ("bin", True), ("srv", True)]
    assert d.show_fin_check.isChecked() and d.show_servers_check.isChecked()


def test_welcome_refusal_keeps_settings_checkbox(child_settings):
    d, host = child_settings
    host.result = False
    assert d._apply_welcome_servers(True) is False
    assert not d.show_servers_check.isChecked()


def test_welcome_updates_saved_and_synced(child_settings):
    d, _host = child_settings
    assert d._apply_welcome_updates(True) is True
    assert d.config.get("check_updates") is True
    assert d.check_updates_check.isChecked()


def _welcome(qapp, tmp_path, monkeypatch, **kwargs):
    from hranilka.core import config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui.welcome import WelcomeDialog
    return WelcomeDialog(cfgmod.Config(), **kwargs)


def _finish(dlg):
    dlg._stack.setCurrentIndex(dlg._stack.count() - 1)
    dlg._on_next()


def test_welcome_update_choice_off_by_default_and_saved(qapp, tmp_path, monkeypatch):
    dlg = _welcome(qapp, tmp_path, monkeypatch)
    assert not dlg.check_updates_box.isChecked()
    dlg.check_updates_box.setChecked(True)
    _finish(dlg)
    assert dlg.config.get("check_updates") is True


def test_welcome_refused_choice_keeps_dialog_open(qapp, tmp_path, monkeypatch):
    dlg = _welcome(qapp, tmp_path, monkeypatch, on_updates_changed=lambda _v: False)
    dlg.check_updates_box.setChecked(True)
    dlg.show()
    _finish(dlg)
    assert dlg.isVisible() and not dlg.check_updates_box.isChecked()
    dlg.close()


def test_welcome_text_has_space_after_colon(qapp, tmp_path, monkeypatch):
    dlg = _welcome(qapp, tmp_path, monkeypatch)
    texts = [w.text() for w in dlg.findChildren(QLabel)]
    assert not any("момент:Настройки" in t for t in texts)
    assert any("момент: Настройки" in t for t in texts)
