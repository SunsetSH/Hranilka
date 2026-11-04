"""Пиксельные иконки дерева 16x16 (7.5). Рисунки заданы массивами строк:
один символ = один пиксель, палитра: '.' — прозрачно, 'o' — контур (цвет
текста темы), 'f' — заливка (производная от цвета текста и фона через
theme.mix). Рендер кэшируется по (вид, цвет, заливка, devicePixelRatio).

Карта, кошелёк и сервер различаются силуэтом, а не деталями: в дереве иконки
мелкие. Карта — широкий прямоугольник с полосой, кошелёк — круглая монета с
символом биткоина, сервер — узкая высокая стойка."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap

from hranilka.core.nodetypes import (ACCOUNT, CARD, FOLDER, SERVER, SERVICE,
                                     WALLET)
from hranilka.ui import theme

SIZE = 16
FAVORITE = "favorite"

ICON_ART: dict[str, tuple[str, ...]] = {
    FOLDER: (
        "................",
        "................",
        "..ooooo.........",
        ".offfffo........",
        ".offfffoooooooo.",
        ".offfffffffffffo",
        ".offfffffffffffo",
        ".offfffffffffffo",
        ".offfffffffffffo",
        ".offfffffffffffo",
        ".offfffffffffffo",
        ".offfffffffffffo",
        ".offfffffffffffo",
        "..oooooooooooooo",
        "................",
        "................",
    ),
    SERVICE: (
        "................",
        "................",
        ".oooooooooooooo.",
        ".offffffffffffo.",
        ".oooooooooooooo.",
        ".offffffffffffo.",
        ".of.........ffo.",
        ".of.oooooo..ffo.",
        ".of.........ffo.",
        ".of.oooo....ffo.",
        ".of.........ffo.",
        ".offffffffffffo.",
        ".oooooooooooooo.",
        "................",
        "................",
        "................",
    ),
    ACCOUNT: (
        "................",
        "................",
        "...oooo.........",
        "..offffo........",
        ".offooffo.......",
        ".offooffo.......",
        "..offffo........",
        "...oofo.........",
        "....ofo.........",
        "....ofoo........",
        "....ofo.........",
        "....ofoo........",
        "....ofo.........",
        "....ooo.........",
        "................",
        "................",
    ),
    CARD: (
        "................",
        "................",
        "................",
        ".oooooooooooooo.",
        ".offffffffffffo.",
        ".oooooooooooooo.",
        ".oooooooooooooo.",
        ".oooooooooooooo.",
        ".offffffffffffo.",
        ".offffffffffffo.",
        ".ofoooofooofffo.",
        ".offffffffffffo.",
        ".oooooooooooooo.",
        "................",
        "................",
        "................",
    ),
    WALLET: (
        "................",
        ".....oooooo.....",
        "...ooffffffoo...",
        "..offffofofffo..",
        ".offffooooffffo.",
        ".offffofffofffo.",
        ".offffofffofffo.",
        ".offffooooffffo.",
        ".offffofffofffo.",
        ".offffofffofffo.",
        ".offffooooffffo.",
        "..offffofofffo..",
        "...ooffffffoo...",
        ".....oooooo.....",
        "................",
        "................",
    ),
    SERVER: (
        "................",
        "...oooooooooo...",
        "...offffffffo...",
        "...ofoooooofo...",
        "...offffffffo...",
        "...ofoooooofo...",
        "...offffffffo...",
        "...ofoooooofo...",
        "...offffffffo...",
        "...offffffffo...",
        "...offffooffo...",
        "...offffooffo...",
        "...offffffffo...",
        "...oooooooooo...",
        "..oooooooooooo..",
        "................",
    ),
    FAVORITE: (
        "................",
        ".......oo.......",
        "......offo......",
        "......offo......",
        ".....offffo.....",
        "oooooofffffoooo.",
        ".offfffffffffo..",
        "..offfffffffo...",
        "...offfffffo....",
        "...offfffffo....",
        "..offffooffffo..",
        "..offfo..offfo..",
        ".offo......offo.",
        ".oo..........oo.",
        "................",
        "................",
    ),
}

_cache: dict[tuple[str, str, str, float], QPixmap] = {}


def kinds() -> tuple[str, ...]:
    return tuple(ICON_ART)


def fill_color(text_color: str, bg_color: str) -> str:
    """Заливка: цвет текста, приглушённый в сторону фона."""
    return theme.mix(text_color, bg_color, 0.55)


def render(kind: str, outline: str, fill: str, dpr: float = 1.0) -> QPixmap:
    """QPixmap 16x16 (логических) для вида иконки; кэшируется."""
    key = (kind, QColor(outline).name(), QColor(fill).name(), dpr)
    pm = _cache.get(key)
    if pm is not None:
        return pm
    art = ICON_ART[kind]
    scale = max(1, round(dpr))
    colors = {"o": QColor(key[1]).rgba(), "f": QColor(key[2]).rgba()}
    img = QImage(SIZE * scale, SIZE * scale, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    for y, row in enumerate(art):
        for x, ch in enumerate(row):
            rgba = colors.get(ch)
            if rgba is None:
                continue
            for dy in range(scale):
                for dx in range(scale):
                    img.setPixel(x * scale + dx, y * scale + dy, rgba)
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(scale)
    if len(_cache) > 512:
        _cache.clear()
    _cache[key] = pm
    return pm


def make_icon(kind: str, text_color: str, bg_color: str,
              dpr: float = 1.0) -> QIcon:
    """QIcon: обычный вид — контур цветом текста; для выделенной строки
    (фон = цвет текста) — контур цветом фона дерева."""
    icon = QIcon()
    icon.addPixmap(render(kind, text_color, fill_color(text_color, bg_color), dpr),
                   QIcon.Mode.Normal)
    icon.addPixmap(render(kind, bg_color, fill_color(bg_color, text_color), dpr),
                   QIcon.Mode.Selected)
    return icon


def clear_cache() -> None:
    _cache.clear()
