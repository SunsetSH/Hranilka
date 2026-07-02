"""FlowLayout — раскладка, переносящая элементы на новую строку при нехватке
ширины (стандартный приём Qt). WrappingTabWidget использует её, чтобы кнопки
вкладок выстраивались в 2+ ряда при сжатии окна."""

from PySide6.QtCore import Qt, QPoint, QRect, QSize
from PySide6.QtWidgets import QLayout, QWidget, QVBoxLayout, QPushButton, QStackedWidget


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, hspacing=4, vspacing=4):
        super().__init__(parent)
        self._hspace = hspacing
        self._vspace = vspacing
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_height = effective.x(), effective.y(), 0

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._hspace
            if next_x - self._hspace > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._vspace
                next_x = x + hint.width() + self._hspace
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + m.bottom()


class WrappingTabWidget(QWidget):
    """Замена QTabWidget: панель кнопок-вкладок на FlowLayout (переносятся в
    несколько рядов) + QStackedWidget со страницами."""

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._bar = QWidget(self)
        self._bar_layout = FlowLayout(self._bar, margin=0, hspacing=2, vspacing=2)
        self._stack = QStackedWidget(self)

        outer.addWidget(self._bar)
        outer.addWidget(self._stack, 1)
        self._buttons = []

    def addTab(self, widget, title):
        index = self._stack.count()
        btn = QPushButton(title, self._bar)
        btn.setCheckable(True)
        btn.setProperty("tabButton", True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda checked=False, i=index: self.setCurrentIndex(i))
        self._bar_layout.addWidget(btn)
        self._buttons.append(btn)
        self._stack.addWidget(widget)
        if index == 0:
            btn.setChecked(True)
        return index

    def setCurrentIndex(self, index):
        if 0 <= index < self._stack.count():
            self._stack.setCurrentIndex(index)
            for i, b in enumerate(self._buttons):
                b.setChecked(i == index)

    def currentIndex(self):
        return self._stack.currentIndex()

    def count(self):
        return self._stack.count()

    def tab_buttons(self):
        return list(self._buttons)

    def one_row_width(self):
        """Ширина, при которой все кнопки-вкладки помещаются ровно в один ряд:
        сумма их sizeHint-ширин + горизонтальные промежутки FlowLayout."""
        if not self._buttons:
            return 0
        # Полируем кнопки, чтобы sizeHint учитывал padding/border из таблицы
        # стилей ещё до показа окна (иначе ряд считается уже реального).
        for b in self._buttons:
            b.ensurePolished()
        total = sum(b.sizeHint().width() for b in self._buttons)
        total += self._bar_layout._hspace * (len(self._buttons) - 1)
        m = self._bar_layout.contentsMargins()
        return total + m.left() + m.right()
