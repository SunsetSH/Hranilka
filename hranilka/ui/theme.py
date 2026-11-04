"""Темизация: единый стиль для окон и набор модальных диалогов, которые
учитывают настройки (шрифт, размер, цвета, фон) — в отличие от стандартных
QMessageBox/QInputDialog."""
import logging
from dataclasses import dataclass

from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QComboBox, QListWidget,
                               QListWidgetItem, QLayout)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QTimer

from hranilka.ui import sounds
from hranilka.ui.msg_icons import msg_icon


def apply_screenshot_protect(widget, enabled: bool = True) -> bool:
    """Применить/снять скриншот-защиту окна (Windows SetWindowDisplayAffinity).

    Единая точка для главного окна и диалогов (M-01). Возвращает True при
    успехе; провал WinAPI (BOOL=0) не маскируется — GetLastError уходит в лог,
    вызыватель может показать фактический статус. Вне Windows — False."""
    WDA_NONE = 0x00000000
    WDA_EXCLUDEFROMCAPTURE = 0x00000011
    try:
        import ctypes
        user32 = ctypes.windll.user32          # AttributeError вне Windows
        ok = bool(user32.SetWindowDisplayAffinity(
            int(widget.winId()),
            WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE))
        if not ok:
            logging.warning(
                "SetWindowDisplayAffinity(enabled=%s) не применилась: "
                "WinAPI error %s", enabled, ctypes.windll.kernel32.GetLastError())
        return ok
    except Exception as e:                      # noqa: BLE001 — не-Windows/нет API
        logging.warning("Скриншот-защита недоступна: %s", e)
        return False


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


def _lum(c) -> float:
    q = QColor(c)

    def f(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(q.red()) + 0.7152 * f(q.green()) + 0.0722 * f(q.blue())


def contrast(c1, c2) -> float:
    """Контраст двух цветов по WCAG (1.0 … 21.0)."""
    hi, lo = sorted((_lum(c1), _lum(c2)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def ensure_contrast(preferred, bgs, candidates, min_ratio):
    """Классический цвет оставляем как есть, пока он различим на всех фонах
    bgs; иначе берём первого подходящего кандидата (или самого контрастного).
    Так тема по умолчанию остаётся пиксель-в-пиксель прежней, а на нестандартных
    фонах границы/disabled не пропадают."""
    def score(c):
        return min(contrast(c, bg) for bg in bgs)
    if score(preferred) >= min_ratio:
        return QColor(preferred).name().upper()
    best = preferred
    for c in candidates:
        if score(c) >= min_ratio:
            return QColor(c).name().upper()
        if score(c) > score(best):
            best = c
    return QColor(best).name().upper()


def hover_color(tree_bg, main_bg, text) -> str:
    """Цвет подсветки строки/ветки дерева. main_bg, если он заметно отличается
    от tree_bg; иначе (одинаковые фоны) — оттенок к цвету текста, чтобы ховер
    не был невидимым."""
    if contrast(tree_bg, main_bg) >= 1.15:
        return QColor(main_bg).name()
    return mix(tree_bg, text, 0.15)


@dataclass(frozen=True)
class Palette:
    border: str
    border_hover: str
    disabled_fg: str
    disabled_border: str
    disabled_bg: str
    danger: str


def palette(config) -> Palette:
    """Служебные цвета (границы, disabled, «опасность»), выведенные из фона темы.
    На теме по умолчанию совпадают с прежними константами."""
    _, _, text, tree_bg, main_bg = _colors(config)
    bgs = (tree_bg, main_bg)
    towards = lambda t: mix(tree_bg, text, t)           # noqa: E731
    border = ensure_contrast('#808080', bgs, [towards(0.5), towards(0.7), towards(0.9)], 1.8)
    border_hover = ensure_contrast('#A0A0A0', (tree_bg,), [towards(0.7), towards(0.9)], 2.5)
    disabled_fg = ensure_contrast('#808080', (main_bg,), [towards(0.5), towards(0.7)], 2.0)
    disabled_bg = ensure_contrast('#3A3A3A', (tree_bg,), [towards(0.15), towards(0.25)], 1.1)
    disabled_border = ensure_contrast('#585858', (disabled_bg,), [towards(0.35), towards(0.5)], 1.3)
    danger = ensure_contrast('#C0392B', (main_bg,),
                             [QColor('#C0392B').lighter(150).name(),
                              QColor('#C0392B').darker(160).name()], 1.5)
    return Palette(border, border_hover, disabled_fg, disabled_border, disabled_bg, danger)



def _colors(config):
    return (
        config.get("font", "Courier New"),
        config.get("font_size", 14),
        config.get("text_color", "#FFFFFF"),
        config.get("tree_bg_color", "#0000AA"),
        config.get("main_bg_color", "#0000AA"),
    )


def _bevel_mode(config) -> bool:
    """True, если рамки кнопок/полей рисует RetroStyle (chrome_style != flat):
    тогда в QSS у этих виджетов нет border (иначе QSS рисует рамку сам)."""
    return config.get("chrome_style", "flat") not in ("flat", None, "")


def _field_css(bevel, text_color, tree_bg, main_bg, font_name, font_size, p) -> str:
    base = (f"background-color: {tree_bg}; color: {text_color}; "
            f"font-family: '{font_name}'; font-size: {font_size}px;")
    dis = (f"color: {mix(text_color, tree_bg, 0.6)}; "
           f"background-color: {mix(tree_bg, main_bg, 0.6)};")
    dis_border = f" border: 2px inset {mix(p.border, tree_bg, 0.45)};"
    if not bevel:
        return f"""QLineEdit, QTextEdit, QListWidget, QDateTimeEdit, QDateEdit, QSpinBox, QComboBox {{
            {base} border: 2px inset {p.border}; padding: 5px; }}
        QLineEdit:disabled, QTextEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
            {dis}{dis_border} }}"""
    # Рамку QLineEdit/QTextEdit/QListWidget рисует RetroStyle (padding +2 px под фаску).
    return f"""QLineEdit, QTextEdit, QListWidget {{ {base} padding: 7px; }}
        QDateTimeEdit, QDateEdit, QSpinBox, QComboBox {{
            {base} border: 2px inset {p.border}; padding: 5px; }}
        QLineEdit:disabled, QTextEdit:disabled {{ {dis} }}
        QSpinBox:disabled, QComboBox:disabled {{ {dis}{dis_border} }}"""


def _button_css(bevel, text_color, tree_bg, main_bg, font_name, font_size, p) -> str:
    dis = (f"color: {mix(text_color, tree_bg, 0.6)}; "
           f"background-color: {mix(tree_bg, main_bg, 0.6)};")
    face = (f"background-color: {tree_bg}; color: {text_color}; "
            f"font-family: '{font_name}'; font-size: {font_size}px; font-weight: bold;")
    if not bevel:
        return f"""QPushButton {{ {face} border: 2px outset {p.border}; padding: 5px 15px; }}
        QPushButton:hover {{ border: 2px inset {p.border}; background-color: {main_bg}; }}
        QPushButton:disabled {{ {dis}
            border: 2px outset {mix(p.border, tree_bg, 0.45)}; }}
        QPushButton:disabled:hover {{ border: 2px outset {mix(p.border, tree_bg, 0.45)};
            background-color: {mix(tree_bg, main_bg, 0.6)}; }}"""
    return f"""QPushButton {{ {face} padding: 7px 17px; }}
        QPushButton:hover {{ background-color: {main_bg}; }}
        QPushButton:disabled, QPushButton:disabled:hover {{ {dis} }}"""


def widget_styles(config):
    """Общие правила для элементов управления (без корневого фона)."""
    font_name, font_size, text_color, tree_bg, main_bg = _colors(config)
    p = palette(config)
    bevel = _bevel_mode(config)
    return f"""
        QLabel {{ color: {text_color}; background: transparent;
            font-family: '{font_name}'; font-size: {font_size}px; }}
        QLabel[heading="true"] {{ font-size: {font_size + 2}px; font-weight: bold; }}
        QLabel:disabled {{ color: {mix(text_color, main_bg, 0.6)}; }}
        {_field_css(bevel, text_color, tree_bg, main_bg, font_name, font_size, p)}
        QComboBox QAbstractItemView {{
            background-color: {tree_bg}; color: {text_color};
            selection-background-color: {main_bg}; selection-color: {text_color};
            border: 1px solid {p.border};
        }}
        QGroupBox {{ border: 1px solid {p.border}; margin-top: 10px; padding: 8px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px;
            color: {text_color}; background-color: {main_bg};
            font-family: '{font_name}'; font-size: {font_size - 2}px; }}
        {_button_css(bevel, text_color, tree_bg, main_bg, font_name, font_size, p)}
        QPushButton[tabButton="true"]:checked {{ background-color: {main_bg};{"" if bevel else f" border: 2px inset {p.border};"} }}
        QPushButton[treeTool="true"] {{ padding: 0px; }}
        QPushButton[treeTool="true"]:checked {{ background-color: {main_bg};{"" if bevel else f" border: 2px inset {p.border};"} }}
        QCheckBox, QRadioButton {{ color: {text_color}; font-family: '{font_name}'; font-size: {font_size}px;
            spacing: 8px; background: transparent; }}
        QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px;
            border: 2px inset {p.border}; background-color: {tree_bg}; }}
        QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background-color: {text_color}; }}
        QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover {{ border: 2px inset {p.border_hover}; }}
        QCheckBox:disabled, QRadioButton:disabled {{ color: {p.disabled_fg}; }}
        QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
            border: 2px inset {p.disabled_border}; background-color: {p.disabled_bg}; }}
        QMenu {{ background-color: {tree_bg}; color: {text_color}; border: 2px outset {p.border}; }}
        QMenu::item:selected {{ background-color: {main_bg}; }}
        QMenuBar {{ background-color: {tree_bg}; color: {text_color}; border: 1px solid {p.border}; }}
        QProgressBar {{ background-color: {tree_bg}; color: {text_color};
            border: 2px inset {p.border}; text-align: center; min-height: 16px;
            font-family: '{font_name}'; font-size: {font_size}px; }}
        QProgressBar::chunk {{ background-color: {text_color}; width: 10px; margin: 1px; }}
        QStatusBar {{ background-color: {tree_bg}; color: {text_color}; border: 1px solid {p.border};
            font-family: '{font_name}'; font-size: {font_size}px; }}
        QStatusBar QLabel {{ font-family: '{font_name}'; font-size: {font_size}px; }}
        QStatusBar::item {{ border: none; }}
        QStatusBar QLabel[statusPanel="true"] {{ border: 1px inset {p.border};
            padding: 1px 8px; }}
        QStatusBar QLabel[statusPanel="true"]:disabled {{ color: {p.disabled_fg}; }}
        QScrollBar:vertical {{ background-color: {main_bg}; width: 16px; margin: 0;
            border: 1px solid {p.border}; }}
        QScrollBar:horizontal {{ background-color: {main_bg}; height: 16px; margin: 0;
            border: 1px solid {p.border}; }}
        QScrollBar::handle:vertical {{ background-color: {tree_bg}; border: 2px outset {p.border};
            min-height: 24px; }}
        QScrollBar::handle:horizontal {{ background-color: {tree_bg}; border: 2px outset {p.border};
            min-width: 24px; }}
        QScrollBar::handle:hover {{ border: 2px inset {p.border}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none;
            background: none; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background-color: {main_bg}; }}
    """


def header_styles(config):
    """Стили кастомного заголовка окна (хедер в цвет фона, название — темой)."""
    font_name, font_size, text_color, tree_bg, main_bg = _colors(config)
    p = palette(config)
    if _bevel_mode(config):     # рамку рисует RetroStyle
        title_btn = (f"#titleSettings {{ background-color: {tree_bg}; color: {text_color}; "
                     f"padding: 5px 14px; font-weight: bold; }}")
    else:
        title_btn = (f"#titleSettings {{ background-color: {tree_bg}; color: {text_color}; "
                     f"border: 2px outset {p.border}; padding: 3px 12px; font-weight: bold; }} "
                     f"#titleSettings:hover {{ border: 2px inset {p.border}; }}")
    return f"""
        #titleBar {{ background-color: {main_bg}; border-bottom: 1px solid {p.border}; }}
        #appTitle {{ color: {text_color}; background: transparent;
            font-family: '{font_name}'; font-size: {font_size + 5}px; font-weight: bold;
            letter-spacing: 2px; }}
        {title_btn}
        #winBtn, #winCloseBtn {{ background-color: {tree_bg}; color: {text_color};
            border: 1px solid {p.border}; font-weight: bold; }}
        #winBtn:hover {{ background-color: {main_bg}; }}
        #winCloseBtn:hover {{ background-color: {p.danger}; color: #FFFFFF; }}
    """


def main_stylesheet(config):
    font_name, font_size, text_color, tree_bg, main_bg = _colors(config)
    return (f"QMainWindow {{ background-color: {main_bg}; color: {text_color}; "
            f"font-family: '{font_name}'; font-size: {font_size}px; }}"
            + widget_styles(config) + header_styles(config))


def dialog_stylesheet(config):
    font_name, font_size, text_color, tree_bg, main_bg = _colors(config)
    p = palette(config)
    return (f"QDialog {{ background-color: {main_bg}; color: {text_color}; "
            f"font-family: '{font_name}'; font-size: {font_size}px; }}"
            f"#dialogFrame {{ border: 2px solid {p.border}; background-color: {main_bg}; }}"
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
        self.close_btn.setToolTip("Закрыть")
        self.close_btn.setAccessibleName("Закрыть")
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
        # Провал не глотается: apply_screenshot_protect пишет причину в лог.
        if self.config.get("screenshot_protect", False):
            apply_screenshot_protect(self, True)
        # Windows иногда подменяет запрошенную геометрию окна УЖЕ ПОСЛЕ того,
        # как Qt разложил формы под старый размер (см. предупреждение
        # QWindowsWindow::setGeometry на мониторах с нестандартным DPI/масштабом —
        # запрошенный размер отличается от «Resulting geometry»). До следующего
        # пересчёта раскладки (например, при перетаскивании окна) некоторые поля
        # формы остаются сжатыми — их sizeHint был посчитан под неактуальный
        # размер. Пересчитываем раскладку один раз, когда событийный цикл
        # освободится и система уже применит финальную геометрию.
        QTimer.singleShot(0, self._relayout_after_show)

    def _relayout_after_show(self):
        """Принудительно пересчитать все вложенные layout'ы под текущую
        (уже финальную) геометрию окна — см. showEvent.

        Некоторые диалоги (SettingsDialog) фиксируют высоту явным resize() при
        открытии — под тогдашние, ещё не пересчитанные sizeHint'ы полей. Раз
        содержимому теперь может требоваться больше места, отдаём его: высоту
        окна увеличиваем до актуального sizeHint (никогда не уменьшаем — окно
        не должно «прыгать» меньше того, что пользователь мог уже увидеть)."""
        for layout in self.findChildren(QLayout):
            layout.invalidate()
            layout.activate()
        screen = self.screen().availableGeometry()
        target = min(self.sizeHint().height(), self._max_height())
        if target > self.height():
            self.resize(self.width(), target)
        # Окно не должно уходить за нижний край экрана.
        overflow = self.frameGeometry().bottom() - screen.bottom()
        if overflow > 0:
            self.move(self.x(), max(screen.top(), self.y() - overflow))

    def _max_height(self):
        """Предел высоты при автоподгонке под содержимое."""
        return self.screen().availableGeometry().height()

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


_ERROR_HINTS = ("ошибк", "не удалось", "сбой", "отклонен", "отмен")


def _guess_icon(title: str) -> str:
    low = title.lower()
    return "error" if any(h in low for h in _ERROR_HINTS) else "info"


def _message_row(lay, kind, label, max_width):
    """Строка «иконка Win98 + текст» для themed_*-диалогов сообщений."""
    row = QHBoxLayout()
    row.setSpacing(14)
    icon = QLabel()
    icon.setPixmap(msg_icon(kind))
    icon.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
    icon.setFixedWidth(msg_icon(kind).width() + 4)
    # Длинные сообщения (пути, тексты ошибок) переносим по словам и ограничиваем
    # ширину — иначе окно растягивалось за пределы экрана (M-14).
    label.setWordWrap(True)
    label.setMaximumWidth(max_width)
    row.addWidget(icon)
    row.addWidget(label, 1)
    lay.addLayout(row)


def themed_info(config, parent, title, text, icon=None):
    """Информационное сообщение. icon — "info"/"warning"/"error"/"question";
    None — по заголовку («Ошибка…» -> error). Для error/warning звучит
    событие «ошибка» (если включено в настройках)."""
    kind = icon or _guess_icon(title)
    d = ThemedDialog(config, parent)
    d.setWindowTitle(title)
    _message_row(d.body, kind, QLabel(text), 560)
    _buttons_row(d.body, d, "OK")
    if kind in ("error", "warning"):
        sounds.play(config, "error")
    d.exec()


def themed_confirm(config, parent, title, text, icon="question"):
    d = ThemedDialog(config, parent)
    d.setWindowTitle(title)
    _message_row(d.body, icon, QLabel(text), 720)
    _buttons_row(d.body, d, "Да", "Нет")
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


def themed_combo_choice(config, parent, title, label, options):
    """Выбор одного варианта из выпадающего списка. Возвращает (текст, ok)."""
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


def themed_choice(config, parent, title, text, buttons, default_index=-1):
    """Диалог с произвольным рядом кнопок. Возвращает индекс нажатой кнопки
    или None при закрытии без выбора (Esc/крестик).
    default_index — кнопка по умолчанию (Enter); -1 — последняя."""
    d = ThemedDialog(config, parent)
    d.setWindowTitle(title)
    lay = d.body
    _message_row(lay, "question", QLabel(text), 560)
    result = {"index": None}

    def choose(i):
        result["index"] = i
        d.accept()

    row = QHBoxLayout()
    row.addStretch()
    default = default_index if default_index >= 0 else len(buttons) - 1
    for i, caption in enumerate(buttons):
        btn = QPushButton(caption)
        btn.setDefault(i == default)
        btn.clicked.connect(lambda checked=False, idx=i: choose(idx))
        row.addWidget(btn)
    lay.addLayout(row)
    d.exec()
    return result["index"]


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
