"""Кастомный заголовок окна (frameless) с названием в теме и своими кнопками
управления, плюс контейнер с ручным изменением размера по краям/углам."""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt, Signal, QRect, QEvent


class TitleBar(QWidget):
    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()
    settings_requested = Signal()
    bin_requested = Signal()

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self._drag_pos = None
        self.setObjectName("titleBar")
        self.setFixedHeight(36)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 4, 0)
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
        self.bin_btn.setCursor(Qt.PointingHandCursor)
        self.bin_btn.clicked.connect(self.bin_requested.emit)
        self.bin_btn.setVisible(False)
        lay.addWidget(self.bin_btn)

        self.settings_btn = QPushButton("Настройки")
        self.settings_btn.setObjectName("titleSettings")
        self.settings_btn.setFixedHeight(28)  # та же высота, что у кнопок управления
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        lay.addWidget(self.settings_btn)

        self.min_btn = QPushButton("_")
        self.max_btn = QPushButton("□")    # □
        self.close_btn = QPushButton("X")
        self.min_btn.setObjectName("winBtn")
        self.max_btn.setObjectName("winBtn")
        self.close_btn.setObjectName("winCloseBtn")
        for b in (self.min_btn, self.max_btn, self.close_btn):
            b.setFixedSize(42, 28)
            b.setCursor(Qt.PointingHandCursor)
        self.min_btn.clicked.connect(self.minimize_requested.emit)
        self.max_btn.clicked.connect(self.maximize_requested.emit)
        self.close_btn.clicked.connect(self.close_requested.emit)
        lay.addWidget(self.min_btn)
        lay.addWidget(self.max_btn)
        lay.addWidget(self.close_btn)

    def set_maximized(self, maximized):
        self.max_btn.setText("❐" if maximized else "□")  # ❐ / □

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
        if e.button() == Qt.LeftButton and not self._window.isMaximized():
            self._drag_pos = e.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if (self._drag_pos is not None and e.buttons() & Qt.LeftButton
                and not self._window.isMaximized()):
            self._window.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.maximize_requested.emit()


class ResizableContainer(QWidget):
    """Центральный контейнер окна без рамки: по своим краям (слева/справа/снизу
    и нижним углам) позволяет изменять размер окна мышью. Верх отдан заголовку."""

    MARGIN = 6

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self._dir = None
        self._start_geo = None
        self._start_mouse = None
        self.setMouseTracking(True)
        # WA_Hover — получать HoverMove даже когда мышь над дочерним виджетом,
        # чтобы сбрасывать курсор-ресайз при уходе с границы.
        self.setAttribute(Qt.WA_Hover, True)

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
            "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor, "b": Qt.SizeVerCursor,
            "bl": Qt.SizeBDiagCursor, "br": Qt.SizeFDiagCursor,
        }.get(d)

    def _update_cursor(self, pos):
        """Устанавливает курсор-ресайз на границе; снимает его в теле окна.
        unsetCursor() вместо ArrowCursor — иначе дочерние виджеты унаследуют
        курсор родителя вместо использования своего собственного."""
        if self._window.isMaximized():
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
        if not self._window.isMaximized() and self._dir and (e.buttons() & Qt.LeftButton):
            self._do_resize(e.globalPosition().toPoint())
        else:
            self._update_cursor(e.position().toPoint())

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self._window.isMaximized():
            d = self._direction(e.position().toPoint())
            if d:
                self._dir = d
                self._start_geo = QRect(self._window.geometry())
                self._start_mouse = e.globalPosition().toPoint()
                e.accept()

    def mouseReleaseEvent(self, e):
        self._dir = None

    def _do_resize(self, gpos):
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
