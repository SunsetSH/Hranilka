"""Иконки сообщений Win98: рисуются, кэшируются, цвета классические."""
import pytest

from hranilka.ui import msg_icons
from hranilka.ui.msg_icons import msg_icon


def _px(pm, x, y):
    c = pm.toImage().pixelColor(x, y)
    return c.alpha(), (c.red(), c.green(), c.blue())


def test_size_and_cache(qapp):
    a = msg_icon("error", 32)
    assert (a.width(), a.height()) == (32, 32)
    assert msg_icon("error", 32) is a
    assert msg_icon("error", 48).width() == 48
    assert msg_icon("bogus", 32) is msg_icon("info", 32)


@pytest.mark.parametrize("kind", msg_icons.KINDS)
def test_not_empty_and_corner_transparent(qapp, kind):
    pm = msg_icon(kind, 32)
    assert _px(pm, 0, 0)[0] == 0
    img = pm.toImage()
    opaque = sum(1 for x in range(32) for y in range(32)
                 if img.pixelColor(x, y).alpha() > 200)
    assert opaque > 150


def test_classic_colors(qapp):
    err = msg_icon("error", 64)
    a, (r, g, b) = _px(err, 12, 32)           # левая часть красного диска
    assert a == 255 and r > 200 and g < 60 and b < 60
    warn = msg_icon("warning", 64)
    a, (r, g, b) = _px(warn, 32, 52)          # жёлтая нижняя часть треугольника
    assert a == 255 and r > 200 and g > 200 and b < 80
    q = msg_icon("question", 64)
    a, (r, g, b) = _px(q, 7, 32)
    assert a == 255 and b > 200 and r < 60


def test_themed_info_dialog_has_icon_and_grab(qapp, pure_config, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QLabel
    from hranilka.ui import theme
    seen = {}

    def inspect():
        d = QApplication.activeModalWidget()
        icons = [l for l in d.findChildren(QLabel)
                 if l.pixmap() is not None and not l.pixmap().isNull()]
        seen["icons"] = len(icons)
        seen["grab"] = not d.grab().isNull()
        d.accept()
    QTimer.singleShot(50, inspect)
    theme.themed_info(pure_config, None, "Ошибка", "текст")
    assert seen == {"icons": 1, "grab": True}
