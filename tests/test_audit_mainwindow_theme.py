"""Аудит 2026-11, раздел 7 #7: служебные цвета темы выводятся из фона."""
from hranilka.ui import theme


class Cfg(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


def test_default_palette_is_legacy():
    p = theme.palette(Cfg())
    assert (p.border, p.border_hover, p.disabled_fg) == ("#808080", "#A0A0A0", "#808080")
    assert (p.disabled_border, p.disabled_bg, p.danger) == ("#585858", "#3A3A3A", "#C0392B")


def test_default_stylesheet_keeps_legacy_literals():
    qss = theme.main_stylesheet(Cfg()) + theme.dialog_stylesheet(Cfg())
    for lit in ("#808080", "#A0A0A0", "#585858", "#3A3A3A", "#C0392B"):
        assert lit in qss


def test_border_not_invisible_on_gray_background():
    cfg = Cfg(main_bg_color="#808080", tree_bg_color="#808080", text_color="#FFFFFF")
    p = theme.palette(cfg)
    assert p.border != "#808080"
    assert theme.contrast(p.border, "#808080") >= 1.8
    assert p.danger != "#C0392B" or theme.contrast(p.danger, "#808080") >= 1.5


def test_hover_visible_when_backgrounds_equal():
    h = theme.hover_color("#0000AA", "#0000AA", "#FFFFFF")
    assert h != "#0000aa" and theme.contrast(h, "#0000AA") > 1.0


def test_hover_uses_main_bg_when_different():
    assert theme.hover_color("#000000", "#333333", "#FFFFFF") == "#333333"


def test_dialog_close_button_accessible(qapp):
    d = theme.ThemedDialog(Cfg())
    assert d._header.close_btn.accessibleName() == "Закрыть"
