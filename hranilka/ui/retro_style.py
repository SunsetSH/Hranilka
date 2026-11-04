"""RetroStyle: настоящие четырёхцветные фаски Win98 поверх Fusion (7.1) и
чистые функции оформления окна (7.2/7.3).

Решение спайка: QStyleSheetStyle вызывает примитивы базового (прокси-)стиля
(PE_PanelButtonCommand, PE_FrameLineEdit, PE_PanelLineEdit, PE_Frame), если в
QSS у виджета НЕТ ``border`` (цвет/фон/padding остаются в QSS и перекрывают
пресет). Поэтому в режиме chrome_style != flat theme.widget_styles убирает
border у QPushButton/QLineEdit/QTextEdit/QListWidget, а рамку рисует
RetroStyle. Режим ``flat`` не меняет ничего (QSS-рамки как раньше).

Точки расширения для превью (7.7): ``chrome_theme(config)`` -> ChromeTheme,
``paint_bevel``, ``paint_title_background``, ``title_colors`` — чистые, без
состояния приложения."""
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                           QPainterPath)
from PySide6.QtWidgets import (QApplication, QProxyStyle, QStyle,
                               QStyleOption, QStyleOptionButton, QWidget)

from hranilka.ui import theme

CHROME_STYLES = ("flat", "win95", "win98", "xp", "os9")
CHROME_LABELS = {
    "flat": "Плоский (как раньше)",
    "win95": "Windows 95",
    "win98": "Windows 98",
    "xp": "Windows XP",
    "os9": "Mac OS 9",
}
DEFAULT_CHROME = "flat"


def normalize_chrome(value: object) -> str:
    return value if isinstance(value, str) and value in CHROME_STYLES else DEFAULT_CHROME


# ─── Фаски ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BevelColors:
    """Четыре тона фаски: внешний светлый, внутренний светлый (hilight),
    внутренняя тень, внешняя тень (dark)."""
    light: str
    hilight: str
    shadow: str
    dark: str


def bevel_colors(base: str) -> BevelColors:
    """Тона фаски, производные от фона (theme.mix). На тёмных фонах светлые
    грани усиливаются, чтобы фаска оставалась видимой."""
    if theme._lum(base) > 0.4:
        return BevelColors(theme.mix(base, "#FFFFFF", 0.5), theme.mix(base, "#FFFFFF", 0.9),
                           theme.mix(base, "#000000", 0.4), theme.mix(base, "#000000", 0.8))
    return BevelColors(theme.mix(base, "#FFFFFF", 0.25), theme.mix(base, "#FFFFFF", 0.45),
                       theme.mix(base, "#000000", 0.45), theme.mix(base, "#000000", 0.85))


@dataclass(frozen=True)
class ChromeSpec:
    depth: int       # 1 — одинарная фаска, 2 — двойная (Win98)
    radius: int      # скругление рамки кнопок/полей (px), 0 — прямые углы
    title_radius: int


CHROME_SPECS = {
    "flat": ChromeSpec(1, 0, 0),
    "win95": ChromeSpec(2, 0, 0),
    "win98": ChromeSpec(2, 0, 0),
    "xp": ChromeSpec(1, 3, 8),
    "os9": ChromeSpec(2, 0, 0),
}


def _edge(p: QPainter, r: QRect, tl: str, br: str) -> None:
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    if w < 2 or h < 2:
        return
    c_tl, c_br = QColor(tl), QColor(br)
    p.fillRect(x, y, w - 1, 1, c_tl)
    p.fillRect(x, y, 1, h - 1, c_tl)
    p.fillRect(x, y + h - 1, w, 1, c_br)
    p.fillRect(x + w - 1, y, 1, h, c_br)


def paint_bevel(p: QPainter, rect: QRect, c: BevelColors, sunken: bool = False,
                depth: int = 2, radius: int = 0) -> None:
    """Рисует рамку-фаску. raised: светлые грани сверху-слева, тёмные
    снизу-справа; sunken — инверсия."""
    p.save()
    try:
        if radius > 0:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor(c.dark if not sunken else c.shadow))
            p.drawRoundedRect(rect.adjusted(0, 0, -1, -1), radius, radius)
            p.setPen(QColor(c.shadow if sunken else c.hilight))
            p.drawRoundedRect(rect.adjusted(1, 1, -2, -2), max(radius - 1, 0), max(radius - 1, 0))
        elif depth >= 2:
            if sunken:
                outer, inner = (c.shadow, c.hilight), (c.dark, c.light)
            else:
                outer, inner = (c.light, c.dark), (c.hilight, c.shadow)
            _edge(p, rect, *outer)
            _edge(p, rect.adjusted(1, 1, -1, -1), *inner)
        else:
            _edge(p, rect, *((c.shadow, c.hilight) if sunken else (c.hilight, c.shadow)))
    finally:
        p.restore()


# ─── Тема окна (чистые данные) ───────────────────────────────────────────────

@dataclass(frozen=True)
class ChromeTheme:
    style: str
    text: str
    main_bg: str
    tree_bg: str
    border: str
    danger: str
    bevel: BevelColors

    @property
    def spec(self) -> ChromeSpec:
        return CHROME_SPECS[self.style]


def make_chrome_theme(style: str, text: str, tree_bg: str, main_bg: str,
                      border: str, danger: str) -> ChromeTheme:
    st = normalize_chrome(style)
    return ChromeTheme(st, text, main_bg, tree_bg, border, danger, bevel_colors(main_bg))


def chrome_theme(config) -> ChromeTheme:
    """ChromeTheme из конфига; цвета пользователя — источник, пресет только
    выводит из них производные."""
    _, _, text, tree_bg, main_bg = theme._colors(config)
    p = theme.palette(config)
    return make_chrome_theme(config.get("chrome_style", DEFAULT_CHROME),
                             text, tree_bg, main_bg, p.border, p.danger)


def title_colors(th: ChromeTheme, active: bool) -> tuple[str, str, str]:
    """(цвет слева/сверху, цвет справа/снизу, цвет текста) заголовка."""
    bg, tx = th.main_bg, th.text
    if th.style == "flat":
        return bg, bg, tx
    if active:
        a, b = {"win95": (0.35, 0.35), "win98": (0.35, 0.10),
                "xp": (0.18, 0.42), "os9": (0.10, 0.10)}[th.style]
        c1, c2 = theme.mix(bg, tx, a), theme.mix(bg, tx, b)
        fg = tx
    else:
        c1, c2 = theme.mix(bg, tx, 0.10), theme.mix(bg, tx, 0.05)
        fg = theme.mix(tx, bg, 0.45)
    fg = theme.ensure_contrast(fg, (c1, c2), ["#FFFFFF", "#000000"], 3.0)
    return c1, c2, fg


def paint_title_background(p: QPainter, rect: QRect, th: ChromeTheme, active: bool) -> None:
    """Фон заголовка по chrome_style (градиент Win98 / вертикальный XP со
    скруглёнными верхними углами / платиновый Mac OS 9 / плоский)."""
    c1, c2, _ = title_colors(th, active)
    p.save()
    try:
        p.fillRect(rect, QColor(th.main_bg))
        if th.style == "flat":
            pass
        elif th.style == "win95":
            p.fillRect(rect, QColor(c1))
        elif th.style == "win98":
            g = QLinearGradient(rect.left(), 0, rect.right(), 0)
            g.setColorAt(0.0, QColor(c1))
            g.setColorAt(1.0, QColor(c2))
            p.fillRect(rect, g)
        elif th.style == "xp":
            r = th.spec.title_radius
            path = QPainterPath()
            path.addRoundedRect(rect.adjusted(0, 0, 0, r), r, r)
            g = QLinearGradient(0, rect.top(), 0, rect.bottom())
            g.setColorAt(0.0, QColor(c2))
            g.setColorAt(0.5, QColor(c1))
            g.setColorAt(1.0, QColor(c1).darker(115))
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setClipRect(rect)
            p.fillPath(path, g)
        elif th.style == "os9":
            p.fillRect(rect, QColor(c1))
            stripe = QColor(theme.mix(th.main_bg, th.text, 0.22 if active else 0.12))
            for y in range(rect.top() + 3, rect.bottom() - 3, 2):
                p.fillRect(rect.left() + 3, y, rect.width() - 6, 1, stripe)
        if th.style != "xp":
            p.fillRect(rect.left(), rect.bottom(), rect.width(), 1, QColor(th.border))
    finally:
        p.restore()


# ─── Метрики от шрифта ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class RetroMetrics:
    title_h: int
    btn_w: int
    btn_h: int
    icon: int = 16


def retro_metrics(font: QFont) -> RetroMetrics:
    """Единые размеры заголовка от QFontMetrics (на кегле по умолчанию
    совпадают с прежними 36 / 42x28)."""
    h = QFontMetrics(font).height()
    title_h = max(26, h + 10)
    btn_h = max(18, title_h - 8)
    return RetroMetrics(title_h, btn_h * 3 // 2, btn_h)


# ─── Стиль ───────────────────────────────────────────────────────────────────

class RetroStyle(QProxyStyle):
    """Прокси над Fusion. В режиме flat прозрачен; иначе рисует фаски."""

    def __init__(self, key: str = "Fusion") -> None:
        super().__init__(key)
        self._theme: Optional[ChromeTheme] = None

    def configure(self, th: ChromeTheme) -> bool:
        """Задать тему; True, если что-то изменилось."""
        changed = th != self._theme
        self._theme = th
        return changed

    @property
    def chrome(self) -> str:
        return self._theme.style if self._theme else DEFAULT_CHROME

    @property
    def active(self) -> bool:
        return self._theme is not None and self._theme.style != "flat"

    @staticmethod
    def _unstyled(w: Optional[QWidget]) -> bool:
        """Нет QSS у окна виджета: заливку тогда делаем сами."""
        return w is None or not w.window().styleSheet()

    def _bevel(self, p: QPainter, o: QStyleOption, sunken: bool) -> None:
        th = self._theme
        if th is None:
            return
        paint_bevel(p, o.rect, th.bevel, sunken, th.spec.depth, th.spec.radius)

    def drawPrimitive(self, element, option, painter, widget=None):          # noqa: N802
        if not self.active or self._theme is None:
            return super().drawPrimitive(element, option, painter, widget)
        S = QStyle.PrimitiveElement
        st = option.state
        if element == S.PE_PanelButtonCommand:
            if self._unstyled(widget):
                painter.fillRect(option.rect, option.palette.button())
            self._bevel(painter, option, bool(st & (QStyle.StateFlag.State_Sunken
                                                    | QStyle.StateFlag.State_On)))
            return None
        if element in (S.PE_FrameLineEdit, S.PE_Frame) and (
                element == S.PE_FrameLineEdit or st & QStyle.StateFlag.State_Sunken):
            self._bevel(painter, option, True)
            return None
        if element == S.PE_PanelLineEdit:
            if self._unstyled(widget):
                painter.fillRect(option.rect, option.palette.base())
            self._bevel(painter, option, True)
            return None
        if element == S.PE_FrameFocusRect:
            painter.save()
            pen = painter.pen()
            pen.setColor(QColor(self._theme.text))
            pen.setStyle(Qt.PenStyle.DotLine)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
            painter.restore()
            return None
        return super().drawPrimitive(element, option, painter, widget)

    def drawControl(self, element, option, painter, widget=None):            # noqa: N802
        # CE_PushButton: Fusion рисует панель (PE_PanelButtonCommand → фаска)
        # и подпись через CE_PushButtonLabel; в нажатом состоянии сдвигаем
        # подпись на 1 px.
        if (self.active and element == QStyle.ControlElement.CE_PushButtonLabel
                and isinstance(option, QStyleOptionButton)
                and option.state & (QStyle.StateFlag.State_Sunken)):
            shifted = QStyleOptionButton(option)
            shifted.rect = option.rect.translated(1, 1)
            return super().drawControl(element, shifted, painter, widget)
        return super().drawControl(element, option, painter, widget)


def configure_app(config) -> bool:
    """Применить chrome_style/цвета к установленному RetroStyle. Возвращает
    True, если стиль изменился (виджеты перерисованы)."""
    style = QApplication.style()
    if not isinstance(style, RetroStyle):
        return False
    changed = style.configure(chrome_theme(config))
    if changed:
        for w in QApplication.topLevelWidgets():
            w.update()
    return changed
