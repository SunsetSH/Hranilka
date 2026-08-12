"""FlowLayout — раскладка, переносящая элементы на новую строку при нехватке
ширины (стандартный приём Qt). WrappingTabWidget использует её, чтобы кнопки
вкладок выстраивались в 2+ ряда при сжатии окна."""

from PySide6.QtCore import Qt, QPoint, QRect, QSize, Signal
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
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_height = effective.x(), effective.y(), 0

        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
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

    currentChanged = Signal(int)   # как у QTabWidget: смена активной вкладки

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
        self._tab_visible = []
        # Описание полей, которые можно скрывать в режиме просмотра.
        # Секции сначала регистрируются на внутренней странице, а после
        # addTab() привязываются к индексу вкладки (карточки оборачивают
        # страницы в QScrollArea, поэтому сама страница не лежит в _stack).
        self._empty_sections_by_page = {}
        self._empty_sections_by_tab = {}

    def addTab(self, widget, title):
        index = self._stack.count()
        btn = QPushButton(title, self._bar)
        btn.setCheckable(True)
        btn.setProperty("tabButton", True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda checked=False, i=index: self.setCurrentIndex(i))
        self._bar_layout.addWidget(btn)
        self._buttons.append(btn)
        self._tab_visible.append(True)
        self._stack.addWidget(widget)
        if index == 0:
            btn.setChecked(True)
        return index

    def setCurrentIndex(self, index):
        if (0 <= index < self._stack.count()
                and self._tab_visible[index]):
            changed = index != self._stack.currentIndex()
            self._stack.setCurrentIndex(index)
            for i, b in enumerate(self._buttons):
                b.setChecked(i == index)
            if changed:
                self.currentChanged.emit(index)

    def currentIndex(self):
        return self._stack.currentIndex()

    def count(self):
        return self._stack.count()

    def tab_buttons(self):
        return list(self._buttons)

    def setTabVisible(self, index, visible):
        """Аналог QTabWidget.setTabVisible для самописной панели вкладок."""
        if not 0 <= index < len(self._buttons):
            return
        visible = bool(visible)
        self._tab_visible[index] = visible
        self._buttons[index].setVisible(visible)

    def isTabVisible(self, index):
        return 0 <= index < len(self._tab_visible) and self._tab_visible[index]

    def register_empty_section(self, page, widgets, has_content,
                               available=None, visibility_changed=None,
                               required=False):
        """Зарегистрировать поле/секцию для скрытия пустого в просмотре.

        widgets — один QWidget либо набор виджетов (обычно подпись + поле),
        has_content — функция без аргументов, available — дополнительный
        функциональный флаг (например, включён ли показ серверов).
        visibility_changed нужен строкам из вложенных layout: он обнуляет их
        stretch при скрытии, чтобы справа не оставалась пустая половина строки.
        required оставляет обязательную секцию видимой даже без содержимого.
        """
        if isinstance(widgets, QWidget):
            widgets = (widgets,)
        else:
            widgets = tuple(widgets)
        entry = (widgets, has_content, available, visibility_changed, required)
        self._empty_sections_by_page.setdefault(page, []).append(entry)

    def bind_empty_page(self, page, tab_index):
        """Связать накопленные секции внутренней страницы с вкладкой."""
        self._empty_sections_by_tab[tab_index] = \
            self._empty_sections_by_page.pop(page, [])

    def refresh_empty_visibility(self, editable=False):
        """Применить настройку скрытия пустых полей/вкладок карточки.

        В редактировании все доступные поля и вкладки остаются видимыми. Если
        активная вкладка стала пустой после сохранения, переносим пользователя
        на первую содержательную вкладку (для карточек это «База»).
        """
        config = getattr(self, "config", None)
        hide_empty = True if config is None else bool(
            config.get("hide_empty_card_fields", True))

        visible_tabs = []
        for index in range(self.count()):
            entries = self._empty_sections_by_tab.get(index)
            if entries is None:
                self.setTabVisible(index, True)
                visible_tabs.append(index)
                continue

            section_visible = []
            for (widgets, has_content, available, visibility_changed,
                 required) in entries:
                enabled = True if available is None else bool(available())
                content = enabled and (required or bool(has_content()))
                show = enabled and (editable or not hide_empty or content)
                for widget in widgets:
                    widget.setVisible(show)
                if visibility_changed is not None:
                    visibility_changed(show)
                section_visible.append(show)

            show_tab = bool(section_visible) and any(section_visible)
            self.setTabVisible(index, show_tab)
            if show_tab:
                visible_tabs.append(index)

        # Валидные карточки всегда имеют непустое имя, но fail-safe не даёт
        # самописному стеку остаться без страницы даже на повреждённых данных.
        if not visible_tabs and self.count():
            self.setTabVisible(0, True)
            visible_tabs = [0]
        if visible_tabs and self.currentIndex() not in visible_tabs:
            self.setCurrentIndex(visible_tabs[0])

    def one_row_width(self):
        """Ширина, при которой все кнопки-вкладки помещаются ровно в один ряд:
        сумма их sizeHint-ширин + горизонтальные промежутки FlowLayout."""
        if not self._buttons:
            return 0
        # Полируем кнопки, чтобы sizeHint учитывал padding/border из таблицы
        # стилей ещё до показа окна (иначе ряд считается уже реального).
        buttons = [b for i, b in enumerate(self._buttons)
                   if self._tab_visible[i]]
        if not buttons:
            return 0
        for b in buttons:
            b.ensurePolished()
        total = sum(b.sizeHint().width() for b in buttons)
        total += self._bar_layout._hspace * (len(buttons) - 1)
        m = self._bar_layout.contentsMargins()
        return total + m.left() + m.right()
