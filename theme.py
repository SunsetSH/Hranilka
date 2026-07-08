"""Темизация: единый стиль для окон и набор модальных диалогов, которые
учитывают настройки (шрифт, размер, цвета, фон) — в отличие от стандартных
QMessageBox/QInputDialog."""

from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QComboBox, QListWidget,
                               QListWidgetItem)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QTimer


def mix(c1, c2, t):
    """Линейная интерполяция двух цветов: t=0 → c1, t=1 → c2.

    Нужна, чтобы «приглушённые» варианты (placeholder, вспомогательный текст)
    выводились из цветов темы, а не жёстко зашитого серого — иначе на тёмных/
    светлых темах они теряли читаемость (H-10). Принимает hex-строки '#RRGGBB'."""
    a, b = QColor(c1), QColor(c2)
    t = max(0.0, min(1.0, t))
    r = round(a.red()   + (b.red()   - a.red())   * t)
    g = round(a.green() + (b.green() - a.green()) * t)
    bl = round(a.blue()  + (b.blue()  - a.blue())  * t)
    return QColor(r, g, bl).name()


def _colors(config):
    return (
        config.get("font", "Courier New"),
        config.get("font_size", 14),
        config.get("text_color", "#FFFFFF"),
        config.get("tree_bg_color", "#0000AA"),
        config.get("main_bg_color", "#0000AA"),
    )


def widget_styles(config):
    """Общие правила для элементов управления (без корневого фона)."""
    font_name, font_size, text_color, tree_bg, main_bg = _colors(config)
    return f"""
        QLabel {{ color: {text_color}; background: transparent;
            font-family: '{font_name}'; font-size: {font_size}px; }}
        QLabel[heading="true"] {{ font-size: {font_size + 2}px; font-weight: bold; }}
        QLabel:disabled {{ color: {mix(text_color, main_bg, 0.6)}; }}
        QLineEdit, QTextEdit, QListWidget, QDateTimeEdit, QDateEdit, QSpinBox, QComboBox {{
            background-color: {tree_bg}; color: {text_color}; border: 2px inset #808080;
            padding: 5px; font-family: '{font_name}'; font-size: {font_size}px;
        }}
        QLineEdit:disabled, QTextEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
            color: {mix(text_color, tree_bg, 0.6)};
            background-color: {mix(tree_bg, main_bg, 0.6)};
            border: 2px inset {mix('#808080', tree_bg, 0.45)}; }}
        QComboBox QAbstractItemView {{
            background-color: {tree_bg}; color: {text_color};
            selection-background-color: {main_bg}; selection-color: {text_color};
            border: 1px solid #808080;
        }}
        QGroupBox {{ border: 1px solid #808080; margin-top: 10px; padding: 8px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px;
            color: {text_color}; background-color: {main_bg};
            font-family: '{font_name}'; font-size: {font_size - 2}px; }}
        QPushButton {{ background-color: {tree_bg}; color: {text_color}; border: 2px outset #808080;
            padding: 5px 15px; font-family: '{font_name}'; font-size: {font_size}px; font-weight: bold; }}
        QPushButton:hover {{ border: 2px inset #808080; background-color: {main_bg}; }}
        QPushButton:disabled {{ color: {mix(text_color, tree_bg, 0.6)};
            border: 2px outset {mix('#808080', tree_bg, 0.45)};
            background-color: {mix(tree_bg, main_bg, 0.6)}; }}
        QPushButton:disabled:hover {{ border: 2px outset {mix('#808080', tree_bg, 0.45)};
            background-color: {mix(tree_bg, main_bg, 0.6)}; }}
        QPushButton[tabButton="true"]:checked {{ background-color: {main_bg}; border: 2px inset #808080; }}
        QCheckBox, QRadioButton {{ color: {text_color}; font-family: '{font_name}'; font-size: {font_size}px;
            spacing: 8px; background: transparent; }}
        QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px;
            border: 2px inset #808080; background-color: {tree_bg}; }}
        QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background-color: {text_color}; }}
        QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover {{ border: 2px inset #A0A0A0; }}
        QCheckBox:disabled, QRadioButton:disabled {{ color: #808080; }}
        QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
            border: 2px inset #585858; background-color: #3A3A3A; }}
        QMenu {{ background-color: {tree_bg}; color: {text_color}; border: 2px outset #808080; }}
        QMenu::item:selected {{ background-color: {main_bg}; }}
        QMenuBar {{ background-color: {tree_bg}; color: {text_color}; border: 1px solid #808080; }}
        QStatusBar {{ background-color: {tree_bg}; color: {text_color}; border: 1px solid #808080;
            font-family: '{font_name}'; font-size: {font_size}px; }}
        QStatusBar QLabel {{ font-family: '{font_name}'; font-size: {font_size}px; }}
        QScrollBar:vertical {{ background-color: {main_bg}; width: 16px; margin: 0;
            border: 1px solid #808080; }}
        QScrollBar:horizontal {{ background-color: {main_bg}; height: 16px; margin: 0;
            border: 1px solid #808080; }}
        QScrollBar::handle:vertical {{ background-color: {tree_bg}; border: 2px outset #808080;
            min-height: 24px; }}
        QScrollBar::handle:horizontal {{ background-color: {tree_bg}; border: 2px outset #808080;
            min-width: 24px; }}
        QScrollBar::handle:hover {{ border: 2px inset #808080; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none;
            background: none; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background-color: {main_bg}; }}
    """


def header_styles(config):
    """Стили кастомного заголовка окна (хедер в цвет фона, название — темой)."""
    font_name, font_size, text_color, tree_bg, main_bg = _colors(config)
    return f"""
        #titleBar {{ background-color: {main_bg}; border-bottom: 1px solid #808080; }}
        #appTitle {{ color: {text_color}; background: transparent;
            font-family: '{font_name}'; font-size: {font_size + 5}px; font-weight: bold;
            letter-spacing: 2px; }}
        #titleSettings {{ background-color: {tree_bg}; color: {text_color};
            border: 2px outset #808080; padding: 3px 12px; font-weight: bold; }}
        #titleSettings:hover {{ border: 2px inset #808080; }}
        #winBtn, #winCloseBtn {{ background-color: {tree_bg}; color: {text_color};
            border: 1px solid #808080; font-weight: bold; }}
        #winBtn:hover {{ background-color: {main_bg}; }}
        #winCloseBtn:hover {{ background-color: #C0392B; color: #FFFFFF; }}
    """


def main_stylesheet(config):
    font_name, font_size, text_color, tree_bg, main_bg = _colors(config)
    return (f"QMainWindow {{ background-color: {main_bg}; color: {text_color}; "
            f"font-family: '{font_name}'; font-size: {font_size}px; }}"
            + widget_styles(config) + header_styles(config))


def dialog_stylesheet(config):
    font_name, font_size, text_color, tree_bg, main_bg = _colors(config)
    return (f"QDialog {{ background-color: {main_bg}; color: {text_color}; "
            f"font-family: '{font_name}'; font-size: {font_size}px; }}"
            f"#dialogFrame {{ border: 2px solid #808080; background-color: {main_bg}; }}"
            + widget_styles(config) + header_styles(config))


class _DialogHeader(QWidget):
    """Кастомный заголовок модального окна: название темой + кнопка закрытия,
    перетаскивание окна за заголовок."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self._dialog = dialog
        self._drag = None
        self.setObjectName("titleBar")
        self.setFixedHeight(32)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 4, 0)
        lay.setSpacing(4)
        self.title = QLabel("")
        self.title.setObjectName("appTitle")
        lay.addWidget(self.title)
        lay.addStretch()
        self.close_btn = QPushButton("X")
        self.close_btn.setObjectName("winCloseBtn")
        self.close_btn.setFixedSize(42, 26)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(dialog.reject)
        lay.addWidget(self.close_btn)

    def set_title(self, text):
        # Заголовки хедеров модальных окон — заглавными, в одном стиле с
        # названием программы «ХРАНИЛКА» в главном окне.
        self.title.setText(text.upper())

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self._dialog.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag is not None and (e.buttons() & Qt.LeftButton):
            self._dialog.move(e.globalPosition().toPoint() - self._drag)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag = None


class ThemedDialog(QDialog):
    """Базовый диалог: без системной рамки, с кастомным заголовком в стиле
    программы. Содержимое добавляется в self.body."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setStyleSheet(dialog_stylesheet(config))

        frame = QWidget()
        frame.setObjectName("dialogFrame")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        frame_lay = QVBoxLayout(frame)
        frame_lay.setContentsMargins(0, 0, 0, 0)
        frame_lay.setSpacing(0)
        self._header = _DialogHeader(self)
        frame_lay.addWidget(self._header)

        content = QWidget()
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(10)
        frame_lay.addWidget(content, 1)

    def setWindowTitle(self, title):
        super().setWindowTitle(title)
        self._header.set_title(title)

    def showEvent(self, e):
        super().showEvent(e)
        # Применяем ту же скриншот-защиту, что у главного окна, к диалогу.
        if self.config.get("screenshot_protect", False):
            try:
                import ctypes
                ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x00000011)
            except Exception:
                pass

    def closeEvent(self, e):
        super().closeEvent(e)
        # Frameless-диалог на Windows иногда не возвращает активацию родителю.
        if self.parent():
            self.parent().window().activateWindow()


def _buttons_row(layout, dialog, ok_text="OK", cancel_text=None):
    row = QHBoxLayout()
    row.addStretch()
    ok = QPushButton(ok_text)
    ok.setDefault(True)   # Enter нажатый в диалоге → эта кнопка
    ok.clicked.connect(dialog.accept)
    row.addWidget(ok)
    if cancel_text:
        cancel = QPushButton(cancel_text)
        cancel.clicked.connect(dialog.reject)
        row.addWidget(cancel)
    layout.addLayout(row)


def themed_info(config, parent, title, text):
    d = ThemedDialog(config, parent)
    d.setWindowTitle(title)
    lay = d.body
    lbl = QLabel(text)
    # Длинные сообщения (пути, тексты ошибок) переносим по словам и ограничиваем
    # ширину — иначе окно растягивалось за пределы экрана (M-14).
    lbl.setWordWrap(True)
    lbl.setMaximumWidth(560)
    lay.addWidget(lbl)
    _buttons_row(lay, d, "OK")
    d.exec()


def themed_confirm(config, parent, title, text):
    d = ThemedDialog(config, parent)
    d.setWindowTitle(title)
    lay = d.body
    lay.addWidget(QLabel(text))
    _buttons_row(lay, d, "Да", "Нет")
    return d.exec() == QDialog.Accepted


def themed_input(config, parent, title, label, text=""):
    d = ThemedDialog(config, parent)
    d.setWindowTitle(title)
    d.setMinimumWidth(360)
    lay = d.body
    lay.addWidget(QLabel(label))
    edit = QLineEdit(text)
    lay.addWidget(edit)
    _buttons_row(lay, d, "OK", "Отмена")
    edit.returnPressed.connect(d.accept)        # Enter в поле → подтвердить
    QTimer.singleShot(0, edit.setFocus)         # фокус на поле после показа окна
    ok = d.exec() == QDialog.Accepted
    return edit.text(), ok


def themed_choice(config, parent, title, label, options):
    d = ThemedDialog(config, parent)
    d.setWindowTitle(title)
    d.setMinimumWidth(320)
    lay = d.body
    lay.addWidget(QLabel(label))
    combo = QComboBox()
    combo.addItems(options)
    lay.addWidget(combo)
    _buttons_row(lay, d, "OK", "Отмена")
    ok = d.exec() == QDialog.Accepted
    return combo.currentText(), ok


def themed_multiselect(config, parent, title, items, preselected=None):
    """items = [{id, name}]. Возвращает (list_of_ids, ok)."""
    preselected = set(preselected or [])
    d = ThemedDialog(config, parent)
    d.setWindowTitle(title)
    d.setMinimumSize(420, 360)
    lay = d.body
    lay.addWidget(QLabel("Отметьте аккаунты:"))
    lst = QListWidget()
    for it in items:
        item = QListWidgetItem(it["name"])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if it["id"] in preselected else Qt.Unchecked)
        item.setData(Qt.UserRole, it["id"])
        lst.addItem(item)
    lay.addWidget(lst)
    _buttons_row(lay, d, "OK", "Отмена")
    ok = d.exec() == QDialog.Accepted
    chosen = [lst.item(i).data(Qt.UserRole) for i in range(lst.count())
              if lst.item(i).checkState() == Qt.Checked]
    return chosen, ok
