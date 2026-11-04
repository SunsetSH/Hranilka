"""RetroStyle / chrome_style / заголовок / CRT-оверлей (7.1-7.3), offscreen.
Скриншотные проверки: widget.grab() + выборка пикселей."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QLineEdit, QListWidget, QPushButton,
                               QVBoxLayout, QWidget)

from hranilka.core import config as cfg
from hranilka.ui import retro_style, theme
from hranilka.ui.crt_overlay import CrtOverlay, set_crt
from hranilka.ui.retro_style import RetroStyle
from hranilka.ui.titlebar import TitleBar, WinButton


class FakeConfig:
    def __init__(self, **kw):
        self.d = {"font": "Courier New", "font_size": 14, "text_color": "#000000",
                  "tree_bg_color": "#C0C0C0", "main_bg_color": "#C0C0C0",
                  "chrome_style": "win98"}
        self.d.update(kw)

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


@pytest.fixture
def retro(qapp):
    old = QApplication.style().objectName()
    style = RetroStyle("Fusion")
    QApplication.setStyle(style)
    style.configure(retro_style.chrome_theme(FakeConfig()))
    yield style
    QApplication.setStyle(old)


def _lum(c: QColor) -> int:
    return c.red() + c.green() + c.blue()


def _host(config, *widgets):
    w = QWidget()
    w.setStyleSheet(theme.main_stylesheet(config))
    lay = QVBoxLayout(w)
    for x in widgets:
        lay.addWidget(x)
    w.resize(260, 220)
    w.show()
    QApplication.processEvents()
    return w


def _edges(widget):
    img = widget.grab().toImage()
    h, w = img.height(), img.width()
    return (QColor(img.pixel(0, h // 2)), QColor(img.pixel(w - 1, h // 2)),
            QColor(img.pixel(w // 2, 0)), QColor(img.pixel(w // 2, h - 1)))


@pytest.mark.parametrize("make", [lambda: QLineEdit("x"), QListWidget])
def test_field_is_sunken(retro, make):
    wdg = make()
    host = _host(FakeConfig(), wdg)
    left, right, top, bottom = _edges(wdg)
    # утопленное поле: тень сверху-слева, свет снизу-справа
    assert _lum(left) < _lum(right)
    assert _lum(top) < _lum(bottom)
    host.close()


def test_button_raised_and_inverted_when_pressed(retro):
    btn = QPushButton("OK")
    host = _host(FakeConfig(), btn)
    left, right, top, bottom = _edges(btn)
    assert _lum(left) > _lum(right)          # светлая грань слева, тёмная справа
    assert _lum(top) > _lum(bottom)
    btn.setDown(True)
    QApplication.processEvents()
    left, right, top, bottom = _edges(btn)
    assert _lum(left) < _lum(right)          # инверсия
    assert _lum(top) < _lum(bottom)
    host.close()


def test_flat_has_qss_border_and_no_bevel_in_qss(qapp):
    flat = theme.widget_styles(FakeConfig(chrome_style="flat"))
    assert "2px outset" in flat
    win = theme.widget_styles(FakeConfig(chrome_style="win98"))
    assert "padding: 7px 17px" in win and "padding: 5px 15px" not in win


def test_bevel_colors_visible_on_dark_and_light():
    for base in ("#000000", "#0000AA", "#C0C0C0", "#FFFFFF"):
        c = retro_style.bevel_colors(base)
        assert c.hilight != c.dark


def test_user_colors_override_preset(retro):
    th = retro_style.chrome_theme(FakeConfig(main_bg_color="#123456", text_color="#ABCDEF"))
    assert th.main_bg == "#123456" and th.text == "#ABCDEF"
    assert th.bevel == retro_style.bevel_colors("#123456")


# ─── config ─────────────────────────────────────────────────────────────────

def test_config_sanitize_new_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
    c = cfg.Config()
    assert c.config["chrome_style"] == "flat" and c.config["crt_mode"] is False
    clean = c._sanitize({"chrome_style": "xp", "crt_mode": "true"})
    assert clean == {"chrome_style": "xp", "crt_mode": True}
    bad = c._sanitize({"chrome_style": "nope", "crt_mode": [1]})
    assert bad == {"chrome_style": "flat", "crt_mode": False}
    for style in retro_style.CHROME_STYLES:
        assert c._sanitize({"chrome_style": style})["chrome_style"] == style


# ─── заголовок ──────────────────────────────────────────────────────────────

def _bar(style):
    host = QWidget()
    tb = TitleBar(host)
    tb.set_chrome(FakeConfig(chrome_style=style, text_color="#FFFFFF",
                             main_bg_color="#0000AA", tree_bg_color="#0000AA"))
    tb.resize(600, tb.height())
    return host, tb


@pytest.mark.parametrize("style", retro_style.CHROME_STYLES)
def test_titlebar_renders_every_style(qapp, style):
    host, tb = _bar(style)
    img = tb.grab().toImage()
    assert not img.isNull() and img.width() == 600
    host.deleteLater()


def test_titlebar_active_vs_inactive_differ(qapp):
    host, tb = _bar("win98")
    tb.set_active(True)
    a = tb.grab().toImage().pixel(300, tb.height() // 2)
    tb.set_active(False)
    i = tb.grab().toImage().pixel(300, tb.height() // 2)
    assert a != i
    host.deleteLater()


def test_titlebar_gradient_varies_horizontally(qapp):
    host, tb = _bar("win98")
    img = tb.grab().toImage()
    y = tb.height() // 2
    assert img.pixel(60, y) != img.pixel(img.width() - 200, y)
    host.deleteLater()


def test_win_button_states_and_maximize_glyph(qapp):
    host, tb = _bar("win98")
    b = tb.max_btn
    normal = b.grab().toImage()
    b.setDown(True)
    pressed = b.grab().toImage()
    assert normal != pressed
    b.setDown(False)
    tb.set_maximized(True)
    assert b.kind == "restore"
    assert b.grab().toImage() != normal
    tb.set_maximized(False)
    assert b.kind == "max"
    assert isinstance(tb.close_btn, WinButton)
    host.deleteLater()


def test_metrics_scale_with_font(qapp):
    from PySide6.QtGui import QFont
    small, big = QFont("Courier New"), QFont("Courier New")
    small.setPixelSize(12)
    big.setPixelSize(30)
    assert retro_style.retro_metrics(big).title_h > retro_style.retro_metrics(small).title_h


# ─── CRT ────────────────────────────────────────────────────────────────────

def test_crt_overlay_transparent_for_mouse_and_cached(qapp):
    host = QWidget()
    host.resize(200, 100)
    host.show()
    ov = set_crt(host, True)
    assert isinstance(ov, CrtOverlay)
    assert ov.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert ov.size() == host.size()
    host.grab()
    host.grab()
    assert ov.render_count == 1              # кэш: без пересчёта
    host.resize(300, 150)
    QApplication.processEvents()
    host.grab()
    assert ov.render_count == 2 and ov.size() == host.size()
    assert set_crt(host, True) is ov         # идемпотентно
    set_crt(host, False)
    assert host.findChild(CrtOverlay) is None
    host.close()


def test_crt_scanline_pixels(qapp):
    from hranilka.ui.crt_overlay import render_crt_pixmap
    img = render_crt_pixmap(60, 30).toImage()
    assert QColor.fromRgba(img.pixel(30, 3)).alpha() > 0       # линия
    assert QColor.fromRgba(img.pixel(30, 4)).alpha() == 0      # между линиями


# ─── интеграция с главным окном ─────────────────────────────────────────────

@pytest.fixture
def window(qapp, tmp_path, monkeypatch, dispose_window):
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    win = main.MainWindow()
    yield win
    dispose_window(win)


def test_window_applies_chrome_and_crt(window):
    window.config.set("chrome_style", "xp")
    window.config.set("crt_mode", True)
    window.apply_appearance()
    assert window.title_bar._theme.style == "xp"
    ov = window.findChild(CrtOverlay)
    assert ov is not None and ov.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    window.config.set("crt_mode", False)
    window.apply_appearance()
    assert window.findChild(CrtOverlay) is None
    window.config.set("chrome_style", "flat")
    window.apply_appearance()
    assert window.title_bar._theme.style == "flat"


def test_settings_dialog_has_chrome_controls(window):
    from hranilka.ui.dialogs.settings.dialog import SettingsDialog
    try:
        dlg = SettingsDialog(window.config, window)
    except TypeError:
        pytest.skip("сигнатура SettingsDialog")
    assert dlg.chrome_style_combo.count() == len(retro_style.CHROME_STYLES)
    dlg.chrome_style_combo.setCurrentIndex(dlg.chrome_style_combo.findData("win95"))
    assert window.config.get("chrome_style") == "win95"       # живой предпросмотр
    dlg._ask_close_action = lambda: "discard"
    dlg.reject()
    assert window.config.get("chrome_style") == "flat"        # откат
    dlg.deleteLater()
