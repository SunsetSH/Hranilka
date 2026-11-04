"""Кастомный заголовок окна (frameless): рисуется QPainter — градиент по
chrome_style (7.2/7.3), активное/неактивное окно, пиксельная иконка 16x16,
кнопки управления с глифами-линиями, состояния normal/hover/pressed. Плюс
контейнер с ручным изменением размера по краям/углам."""

from typing import Optional

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QAbstractButton
from PySide6.QtCore import Qt, Signal, QRect, QEvent, QSize, QPoint
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from hranilka.ui import theme
from hranilka.ui.retro_style import (ChromeTheme, RetroMetrics, chrome_theme,
                                     make_chrome_theme, paint_bevel,
                                     paint_title_background, retro_metrics,
                                     title_colors)


def window_is_max(window) -> bool:
    """Своё состояние окна (_is_max); isMaximized() у frameless-окна ненадёжен
    (B9). Фолбэк на isMaximized() для окон без _is_max."""
    flag = getattr(window, "_is_max", None)
    return bool(window.isMaximized()) if flag is None else bool(flag)


# Пиксельная иконка приложения 16x16 (замок): X — контур, o — заливка.
APP_ICON = (
    "................",
    "................",
    "......XXXX......",
    ".....XX..XX.....",
    ".....X....X.....",
    ".....X....X.....",
    "....XXXXXXXX....",
    "....XooooooX....",
    "....XooooooX....",
    "....XoooXooX....",
    "....XoooXooX....",
    "....XooooooX....",
    "....XXXXXXXX....",
    "................",
    "................",
    "................",
)


def paint_pixel_icon(p: QPainter, x: int, y: int, rows: tuple[str, ...],
                     palette_map: dict[str, str]) -> None:
    """Рисует пиксельную картинку: 1 символ = 1 пиксель ('.' — пусто)."""
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            col = palette_map.get(ch)
            if col:
                p.fillRect(x + i, y + j, 1, 1, QColor(col))


def paint_glyph(p: QPainter, kind: str, rect: QRect, color: str, face: str) -> None:
    """Глиф кнопки окна линиями (не зависит от шрифта). kind: min/max/restore/close."""
    s = max(8, min(rect.width(), rect.height()) // 2 - 1)
    t = 2 if rect.height() >= 20 else 1
    cx, cy = rect.center().x(), rect.center().y()
    x0, y0 = cx - s // 2, cy - s // 2
    col = QColor(color)
    p.save()
    try:
        if kind == "min":
            p.fillRect(x0, y0 + s - t, s, t, col)
        elif kind == "max":
            p.fillRect(x0, y0, s, t + 1, col)
            p.fillRect(x0, y0, 1, s, col)
            p.fillRect(x0 + s - 1, y0, 1, s, col)
            p.fillRect(x0, y0 + s - 1, s, 1, col)
        elif kind == "restore":
            m = s - 2
            bx, by = x0 + 2, y0                     # задний квадрат
            p.fillRect(bx, by, m, t + 1, col)
            p.fillRect(bx, by, 1, m, col)
            p.fillRect(bx + m - 1, by, 1, m, col)
            p.fillRect(bx, by + m - 1, m, 1, col)
            fx, fy = x0, y0 + 2                     # передний квадрат
            p.fillRect(fx, fy, m, m, QColor(face))
            p.fillRect(fx, fy, m, t + 1, col)
            p.fillRect(fx, fy, 1, m, col)
            p.fillRect(fx + m - 1, fy, 1, m, col)
            p.fillRect(fx, fy + m - 1, m, 1, col)
        elif kind == "close":
            pen = QPen(col, t)
            pen.setCapStyle(Qt.PenCapStyle.SquareCap)
            p.setPen(pen)
            p.drawLine(x0, y0, x0 + s - 1, y0 + s - 1)
            p.drawLine(x0 + s - 1, y0, x0, y0 + s - 1)
    finally:
        p.restore()


def window_button_colors(th: ChromeTheme, kind: str, hover: bool) -> tuple[str, str]:
    """(цвет лица кнопки, цвет глифа)."""
    face = th.main_bg
    if th.style == "flat":
        face = th.tree_bg
    if kind == "close" and (hover or th.style == "xp"):
        face = th.danger
        glyph = "#FFFFFF"
    else:
        if hover:
            face = theme.mix(th.main_bg, th.text, 0.12) if th.style != "flat" else th.main_bg
        glyph = theme.ensure_contrast(th.text, (face,), ["#FFFFFF", "#000000"], 3.0)
    return face, glyph


class WinButton(QAbstractButton):
    """Кнопка управления окном (свернуть/развернуть/закрыть), рисуется кодом."""

    def __init__(self, kind: str, theme_: ChromeTheme, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._theme = theme_
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_theme(self, th: ChromeTheme) -> None:
        self._theme = th
        self.update()

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.update()

    def sizeHint(self) -> QSize:                                        # noqa: N802
        return QSize(42, 28)

    def enterEvent(self, e):                                            # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):                                            # noqa: N802
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):                                            # noqa: N802
        th = self._theme
        down = self.isDown()
        hover = self.underMouse() and self.isEnabled()
        face, glyph = window_button_colors(th, self.kind, hover)
        p = QPainter(self)
        try:
            rect = self.rect()
            p.fillRect(rect, QColor(face))
            if th.style == "flat":
                p.setPen(QColor(th.border))
                p.drawRect(rect.adjusted(0, 0, -1, -1))
            else:
                spec = th.spec
                paint_bevel(p, rect, th.bevel, down, spec.depth, spec.radius)
            if down:
                p.translate(1, 1)
            paint_glyph(p, self.kind, rect, glyph, face)
        finally:
            p.end()


class TitleBar(QWidget):
    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()
    settings_requested = Signal()
    bin_requested = Signal()

    _ICON_PAD = 6

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self._drag_pos = None
        self._active = True
        self._maximized = False
        self._title_fg: Optional[str] = None
        self._theme = make_chrome_theme("flat", "#000000", "#FFFFFF", "#F0F0F0",
                                        "#808080", "#C0392B")
        self.setObjectName("titleBar")
        self.setFixedHeight(36)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(self._ICON_PAD * 2 + 16, 0, 4, 0)
        lay.setSpacing(4)

        self.title = QLabel("ХРАНИЛКА")
        self.title.setObjectName("appTitle")
        lay.addWidget(self.title)
        lay.addStretch()

        # Кнопка корзины — слева от «Настройки». Видимость и текст обновляются
        # через update_bin() (зависит от настройки и числа аккаунтов в корзине).
        self.bin_btn = QPushButton("Корзина (пусто)")
        self.bin_btn.setObjectName("titleSettings")
        self.bin_btn.setFixedHeight(28)
        self.bin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bin_btn.clicked.connect(self.bin_requested.emit)
        self.bin_btn.setVisible(False)
        lay.addWidget(self.bin_btn)

        self.settings_btn = QPushButton("Настройки")
        self.settings_btn.setObjectName("titleSettings")
        self.settings_btn.setFixedHeight(28)  # та же высота, что у кнопок управления
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        lay.addWidget(self.settings_btn)

        self.min_btn = WinButton("min", self._theme)
        self.max_btn = WinButton("max", self._theme)
        self.close_btn = WinButton("close", self._theme)
        self.min_btn.setObjectName("winBtn")
        self.max_btn.setObjectName("winBtn")
        self.close_btn.setObjectName("winCloseBtn")
        # Подписи не рисуются (глифы — линиями), но нужны для a11y и тестов.
        self.min_btn.setText("Свернуть")
        self.max_btn.setText("Развернуть")
        self.close_btn.setText("Закрыть")
        for b in (self.min_btn, self.max_btn, self.close_btn):
            b.setFixedSize(42, 28)
            b.setAccessibleName(b.text())
        self.min_btn.clicked.connect(self.minimize_requested.emit)
        self.max_btn.clicked.connect(self.maximize_requested.emit)
        self.close_btn.clicked.connect(self.close_requested.emit)
        lay.addWidget(self.min_btn)
        lay.addWidget(self.max_btn)
        lay.addWidget(self.close_btn)

    # ─── Оформление ──────────────────────────────────────────────────────────

    def set_chrome(self, config) -> None:
        """Применить chrome_style/цвета/метрики шрифта из конфига (вызывается
        из apply_appearance). Идемпотентно."""
        font_name = config.get("font", "Cascadia Code")
        size = int(config.get("font_size", 14)) + 5
        f = QFont(font_name)
        f.setPixelSize(size)
        f.setBold(True)
        self.apply_theme(chrome_theme(config), retro_metrics(f))

    def apply_theme(self, th: ChromeTheme, m: RetroMetrics) -> None:
        """Чистое применение ChromeTheme + метрик (переиспользуется превью)."""
        self._theme = th
        self.setFixedHeight(m.title_h)
        for b in (self.min_btn, self.max_btn, self.close_btn):
            b.set_theme(th)
            b.setFixedSize(m.btn_w, m.btn_h)
        self.bin_btn.setFixedHeight(m.btn_h)
        self.settings_btn.setFixedHeight(m.btn_h)
        lay = self.layout()
        if lay is not None:
            lay.setContentsMargins(self._ICON_PAD * 2 + m.icon, 0, 4, 0)
        self._sync_title_color()
        self.update()

    def _sync_title_color(self) -> None:
        fg = title_colors(self._theme, self._active)[2]
        if fg != self._title_fg:
            self._title_fg = fg
            self.title.setStyleSheet(f"color: {fg};")

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self._sync_title_color()
            self.update()

    def changeEvent(self, e):                                           # noqa: N802
        if e.type() == QEvent.Type.ActivationChange:
            self.set_active(self.isActiveWindow())
        super().changeEvent(e)

    def paintEvent(self, e):                                            # noqa: N802
        th = self._theme
        p = QPainter(self)
        try:
            paint_title_background(p, self.rect(), th, self._active)
            c1, c2, fg = title_colors(th, self._active)
            y = (self.height() - 16) // 2
            paint_pixel_icon(p, self._ICON_PAD, y, APP_ICON,
                             {"X": fg, "o": theme.mix(fg, c1, 0.6)})
        finally:
            p.end()

    def set_maximized(self, maximized):
        self._maximized = bool(maximized)
        self.max_btn.set_kind("restore" if maximized else "max")
        text = "Восстановить" if maximized else "Развернуть"
        self.max_btn.setText(text)
        self.max_btn.setAccessibleName(text)

    def update_bin(self, count):
        """Обновляет кнопку корзины. Кнопка видна только когда в корзине есть
        аккаунты (пустая корзина скрыта в любом случае). Непустая корзина
        показывается даже при выключенной функции — чтобы можно было
        восстановить ранее удалённые аккаунты."""
        self.bin_btn.setVisible(count > 0)
        if count > 0:
            self.bin_btn.setText(f"Корзина ({count})")

    # Перетаскивание окна за заголовок
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not window_is_max(self._window):
            self._drag_pos = e.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if (self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton
                and not window_is_max(self._window)):
            self._window.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.maximize_requested.emit()


class ResizableContainer(QWidget):
    """Центральный контейнер окна без рамки: по своим краям (слева/справа/снизу
    и нижним углам) позволяет изменять размер окна мышью. Верх отдан заголовку."""

    MARGIN = 6

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self._dir: Optional[str] = None
        self._start_geo: Optional[QRect] = None
        self._start_mouse: Optional[QPoint] = None
        self.setMouseTracking(True)
        # WA_Hover — получать HoverMove даже когда мышь над дочерним виджетом,
        # чтобы сбрасывать курсор-ресайз при уходе с границы.
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def _direction(self, pos):
        m = self.MARGIN
        w, h = self.width(), self.height()
        left = pos.x() <= m
        right = pos.x() >= w - m
        bottom = pos.y() >= h - m
        if bottom and left:
            return "bl"
        if bottom and right:
            return "br"
        if left:
            return "l"
        if right:
            return "r"
        if bottom:
            return "b"
        return None

    @staticmethod
    def _cursor_for(d):
        return {
            "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor, "b": Qt.CursorShape.SizeVerCursor,
            "bl": Qt.CursorShape.SizeBDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
        }.get(d)

    def _update_cursor(self, pos):
        """Устанавливает курсор-ресайз на границе; снимает его в теле окна.
        unsetCursor() вместо ArrowCursor — иначе дочерние виджеты унаследуют
        курсор родителя вместо использования своего собственного."""
        if window_is_max(self._window):
            self.unsetCursor()
            return
        cur = self._cursor_for(self._direction(pos))
        if cur is not None:
            self.setCursor(cur)
        else:
            self.unsetCursor()

    def event(self, e):
        # HoverMove приходит (благодаря WA_Hover) даже когда мышь над дочерним
        # виджетом — это позволяет сбросить курсор при уходе с границы окна.
        if e.type() in (QEvent.Type.HoverMove, QEvent.Type.HoverEnter):
            if not (self._dir and self._start_geo is not None):
                self._update_cursor(e.position().toPoint())
        elif e.type() == QEvent.Type.HoverLeave:
            self.unsetCursor()
        return super().event(e)

    def mouseMoveEvent(self, e):
        if not window_is_max(self._window) and self._dir and (e.buttons() & Qt.MouseButton.LeftButton):
            self._do_resize(e.globalPosition().toPoint())
        else:
            self._update_cursor(e.position().toPoint())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not window_is_max(self._window):
            d = self._direction(e.position().toPoint())
            if d:
                self._dir = d
                self._start_geo = QRect(self._window.geometry())
                self._start_mouse = e.globalPosition().toPoint()
                e.accept()

    def mouseReleaseEvent(self, e):
        self._dir = None

    def _do_resize(self, gpos):
        if self._dir is None or self._start_geo is None or self._start_mouse is None:
            return
        dx = gpos.x() - self._start_mouse.x()
        dy = gpos.y() - self._start_mouse.y()
        g = QRect(self._start_geo)
        minw = self._window.minimumWidth()
        minh = self._window.minimumHeight()
        if "l" in self._dir:
            new_left = min(g.left() + dx, g.right() - minw + 1)
            g.setLeft(new_left)
        if "r" in self._dir:
            g.setRight(max(g.right() + dx, g.left() + minw - 1))
        if "b" in self._dir:
            g.setBottom(max(g.bottom() + dy, g.top() + minh - 1))
        self._window.setGeometry(g)
