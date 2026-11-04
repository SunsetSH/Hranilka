"""Превью темы и кнопки «Сброс» (7.7), offscreen."""
import pytest
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

from hranilka.core.config import RETRO_THEMES
from hranilka.ui.dialogs.settings.theme_preview import ThemePreview


class FakeConfig:
    def __init__(self, **kw):
        self.d = {"font": "Courier New", "font_size": 14, "text_color": "#000000",
                  "tree_bg_color": "#FFFFFF", "main_bg_color": "#F0F0F0",
                  "chrome_style": "flat", "selected_theme": "Classic IDE"}
        self.d.update(kw)

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


def _vals(**kw):
    v = {"font": "Courier New", "font_size": 14, "text_color": "#000000",
         "tree_bg_color": "#FFFFFF", "main_bg_color": "#F0F0F0", "chrome_style": "flat"}
    v.update(kw)
    return v


def _grab(pv):
    pv.resize(320, 260)
    pv.show()
    return pv.grab().toImage()


def _near(img, color, tol=6):
    c = QColor(color)
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            p = QColor(img.pixel(x, y))
            if (abs(p.red() - c.red()) <= tol and abs(p.green() - c.green()) <= tol
                    and abs(p.blue() - c.blue()) <= tol):
                n += 1
    return n


def _label_img(pv):
    return pv.sample_label.grab().toImage()


def test_background_and_text_follow_colors(qapp):
    pv = ThemePreview(_vals(main_bg_color="#102030", text_color="#FFEE00",
                            tree_bg_color="#304050"))
    img = _grab(pv)
    body_px = QColor(pv.grab().toImage().pixel(
        pv.sample_label.mapTo(pv, pv.sample_label.rect().bottomRight()).x() - 2,
        pv.sample_label.mapTo(pv, pv.sample_label.rect().bottomRight()).y() - 2))
    assert body_px.name().upper() == "#102030"
    assert _near(_label_img(pv), "#FFEE00") > 10
    lst = pv.list_widget.mapTo(pv, pv.list_widget.rect().bottomRight())
    assert QColor(img.pixel(lst.x() - 12, lst.y() - 3)).name().upper() == "#304050"

    pv.set_values(_vals(main_bg_color="#A02010", text_color="#00CCFF",
                        tree_bg_color="#505050"))
    img2 = _grab(pv)
    assert _near(img2, "#102030") == 0
    assert _near(_label_img(pv), "#FFEE00") == 0
    assert _near(_label_img(pv), "#00CCFF") > 10
    assert _near(img2, "#A02010") > 500


def test_chrome_style_changes_title(qapp):
    pv = ThemePreview(_vals(chrome_style="flat"))
    a = _grab(pv)
    pv.set_values(_vals(chrome_style="xp"))
    b = _grab(pv)
    ty = pv.title_bar.height() // 2
    assert a.pixel(20, ty) != b.pixel(20, ty) or a != b


def test_debounced_schedule(qapp):
    pv = ThemePreview(_vals())
    pv.schedule(_vals(main_bg_color="#123456"))
    assert pv.styleSheet().count("#123456") == 0       # ещё не применено
    # Под нагрузкой полного прогона таймер срабатывает позже — опрос с запасом.
    for _ in range(100):
        if "#123456" in pv.styleSheet():
            break
        QTest.qWait(50)
    assert "#123456" in pv.styleSheet()


def test_does_not_touch_app_style(qapp):
    from PySide6.QtWidgets import QApplication
    before = QApplication.style()
    ThemePreview(_vals(chrome_style="win98"))
    assert QApplication.style() is before


# ─── «Сброс к пресету» в диалоге ─────────────────────────────────────────────

@pytest.fixture
def dialog(qapp, tmp_path, monkeypatch):
    from hranilka.core import config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui.dialogs.settings.dialog import SettingsDialog
    from hranilka.ui.dialogs.settings import appearance
    monkeypatch.setattr(appearance, "SHOW_THEME_PREVIEW", True)
    cfg = cfgmod.Config()
    d = SettingsDialog(cfg)
    yield d
    d.close()


def test_reset_button_restores_preset_color(dialog):
    dialog.theme_combo.setCurrentText("Matrix")
    preset = RETRO_THEMES["Matrix"]
    assert dialog.config.get("text_color") == preset["text"]
    assert not dialog._reset_color_btns["text_color"].isEnabled()
    dialog.config.set("text_color", "#123456")
    dialog._refresh_color_btns()
    btn = dialog._reset_color_btns["text_color"]
    assert btn.isEnabled()
    assert not dialog._reset_color_btns["main_bg_color"].isEnabled()
    btn.click()
    assert dialog.config.get("text_color") == preset["text"]
    assert not btn.isEnabled()


def test_dialog_preview_follows_controls(dialog):
    dialog.config.set("main_bg_color", "#0A0B0C")
    dialog._refresh_color_btns()
    QTest.qWait(200)
    assert "#0A0B0C" in dialog.theme_preview.styleSheet()
    dialog.chrome_style_combo.setCurrentIndex(dialog.chrome_style_combo.findData("xp"))
    QTest.qWait(200)
    assert dialog.theme_preview._pending["chrome_style"] == "xp"


def test_dialog_import_applies_values(dialog, tmp_path, monkeypatch):
    from hranilka.core import theme_io
    from PySide6.QtWidgets import QFileDialog
    p = tmp_path / "t.json"
    theme_io.export_file(p, {"text_color": "#ABCDEF", "chrome_style": "os9",
                             "font_size": 18, "crt_mode": True})
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(p), ""))
    dialog._import_theme()
    assert dialog.config.get("text_color") == "#ABCDEF"
    assert dialog._chrome_style_value() == "os9"
    assert dialog.font_size_combo.currentText() == "18"
    assert dialog.crt_mode_check.isChecked()
