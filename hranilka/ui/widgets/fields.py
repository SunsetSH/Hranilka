"""Поля карточки аккаунта: копируемые строка/дата/текст и интервал в днях
(вынесены из widgets.py, этап 5)."""
import datetime as dt

from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton, QTextEdit, QApplication, QSpinBox)
from PySide6.QtCore import Signal, Qt


class CopyableField(QWidget):
    copy_signal = Signal()

    # Ретро-маркеры кнопки показа пароля (без emoji)
    _MASK_SHOW = "[*]"   # сейчас скрыто (звёздочки) — нажать, чтобы показать
    _MASK_HIDE = "[A]"   # сейчас видно (буквы) — нажать, чтобы скрыть

    def __init__(self, text="", is_password=False, parent=None):
        super().__init__(parent)
        self.is_password = is_password
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.input = QLineEdit(text)
        self.input.setReadOnly(True)
        if is_password:
            self.input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.input)

        self.reveal_btn = None
        if is_password:
            self.reveal_btn = QPushButton(self._MASK_SHOW)
            self.reveal_btn.setFixedWidth(45)
            self.reveal_btn.setCheckable(True)
            self.reveal_btn.setToolTip("Показать / скрыть пароль")
            self.reveal_btn.clicked.connect(self._toggle_reveal)
            layout.addWidget(self.reveal_btn)

        self.copy_btn = QPushButton("[КОП]")
        self.copy_btn.setFixedWidth(60)
        self.copy_btn.clicked.connect(self.do_copy)
        layout.addWidget(self.copy_btn)

    def _toggle_reveal(self):
        shown = self.reveal_btn.isChecked()
        self.input.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.reveal_btn.setText(self._MASK_HIDE if shown else self._MASK_SHOW)

    def do_copy(self):
        text = self.input.text()
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()

    def set_text(self, text): self.input.setText(text)
    def get_text(self): return self.input.text()
    def set_placeholder(self, text): self.input.setPlaceholderText(text)

    def set_editable(self, editable):
        self.input.setReadOnly(not editable)
        self.copy_btn.setVisible(not editable)
        if self.is_password:
            if editable:
                # При редактировании пароль показываем, кнопку показа прячем
                self.input.setEchoMode(QLineEdit.Normal)
                self.reveal_btn.setVisible(False)
            else:
                # В режиме просмотра — снова маскируем
                self.input.setEchoMode(QLineEdit.Password)
                self.reveal_btn.setChecked(False)
                self.reveal_btn.setText(self._MASK_SHOW)
                self.reveal_btn.setVisible(True)

class CopyableDateField(QWidget):
    """Поле даты с маской ввода дд.мм.гггг (+ чч:мм для is_datetime).

    Обычная строка с маской вместо QDateEdit: без календаря и стрелок.
    Поведение маски Qt — как режим Ins: цифра перезаписывает позицию под
    курсором (курсор можно ставить в любое место), Backspace очищает символ,
    точки/двоеточие фиксированы. Пустое или недописанное значение = «не
    задано» (None) — как и раньше, дата не подменяется сегодняшним числом.
    """
    copy_signal = Signal()

    def __init__(self, is_datetime=False, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self._is_datetime = is_datetime
        # strftime-формат отображения (модель дат — стандартный datetime;
        # это граница Qt: AccountData о Qt-типах не знает).
        self.format = "%d.%m.%Y %H:%M" if is_datetime else "%d.%m.%Y"

        self.date_widget = QLineEdit()
        # «0» — необязательная цифра: допускает частично заполненное поле.
        self.date_widget.setInputMask("00.00.0000 00:00" if is_datetime
                                      else "00.00.0000")
        self.date_widget.setToolTip("Формат: дд.мм.гггг чч:мм" if is_datetime
                                    else "Формат: дд.мм.гггг")
        self.date_widget.setReadOnly(True)
        layout.addWidget(self.date_widget)

        self.copy_btn = QPushButton("[КОП]")
        self.copy_btn.setFixedWidth(60)
        self.copy_btn.clicked.connect(self.do_copy)
        layout.addWidget(self.copy_btn)

    def do_copy(self):
        value = self.get_date()
        if value is None:
            return
        QApplication.clipboard().setText(value.strftime(self.format))
        self.copy_signal.emit()

    def set_date(self, value):
        """Принимает datetime.datetime, datetime.date или None.
        None/пусто/неизвестный тип → «не задано».

        datetime — подкласс date, поэтому проверяется первым."""
        if isinstance(value, dt.datetime):
            text = value.strftime(self.format)
        elif isinstance(value, dt.date):
            text = value.strftime("%d.%m.%Y")
            if self._is_datetime:
                text += " 00:00"
        else:
            # None или неизвестный формат — «не задано», а не «сегодня».
            text = ""
        self.date_widget.setText(text)

    def get_date(self):
        """datetime.datetime или None, если дата не задана/не дописана.

        Для is_datetime незаполненное время считается 00:00 — дата без
        времени не должна пропадать при сохранении."""
        raw = self.date_widget.text()          # маска: пусто = "..[ :]"
        date_part = raw[:10].strip(" .")
        if not date_part:
            return None
        try:
            d = dt.datetime.strptime(raw[:10], "%d.%m.%Y").date()
        except ValueError:
            return None
        t = dt.time(0, 0)
        if self._is_datetime:
            try:
                t = dt.datetime.strptime(raw[11:16], "%H:%M").time()
            except ValueError:
                pass                           # время пустое/недописано → 00:00
        return dt.datetime.combine(d, t)

    def set_editable(self, editable):
        self.date_widget.setReadOnly(not editable)
        self.copy_btn.setVisible(not editable)
        
class CopyableTextEdit(QWidget):
    copy_signal = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        layout.addWidget(self.text_edit)
        
        self.btn_layout = QHBoxLayout()
        self.btn_layout.addStretch()
        self.copy_btn = QPushButton("[КОП]")
        self.copy_btn.setFixedWidth(60)
        self.copy_btn.clicked.connect(self.do_copy)
        self.btn_layout.addWidget(self.copy_btn)
        layout.addLayout(self.btn_layout)

    def do_copy(self):
        text = self.get_text()
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()

    def set_text(self, text): self.text_edit.setPlainText(text)
    def get_text(self): return self.text_edit.toPlainText()
    def set_editable(self, editable):
        self.text_edit.setReadOnly(not editable)
        self.copy_btn.setVisible(not editable)


class MaskedTextEdit(CopyableTextEdit):
    """CopyableTextEdit для секретов (recovery-фраза): в режиме просмотра текст
    скрыт («•»), раскрывается кнопкой. У QTextEdit нет echo mode, поэтому
    маскируем подменой отображаемого текста — настоящее значение живёт в
    _secret и в виджет попадает только при показе/редактировании."""

    _MASK_SHOW = CopyableField._MASK_SHOW
    _MASK_HIDE = CopyableField._MASK_HIDE

    def __init__(self, parent=None):
        super().__init__(parent)
        self._secret = ""
        self._editable = False
        self._revealed = False
        self.reveal_btn = QPushButton(self._MASK_SHOW)
        self.reveal_btn.setFixedWidth(45)
        self.reveal_btn.setCheckable(True)
        self.reveal_btn.setToolTip("Показать / скрыть")
        self.reveal_btn.clicked.connect(self._toggle_reveal)
        # Перед [КОП] (после stretch).
        self.btn_layout.insertWidget(1, self.reveal_btn)

    def _toggle_reveal(self):
        self._revealed = self.reveal_btn.isChecked()
        self.reveal_btn.setText(self._MASK_HIDE if self._revealed
                                else self._MASK_SHOW)
        self._refresh()

    def _refresh(self):
        if self._editable or self._revealed:
            shown = self._secret
        else:
            shown = "".join(c if c == "\n" else "•" for c in self._secret)
        self.text_edit.setPlainText(shown)

    def set_text(self, text):
        self._secret = text or ""
        self._refresh()

    def get_text(self):
        # В режиме правки истина — в виджете; в просмотре там может быть маска.
        return self.text_edit.toPlainText() if self._editable else self._secret

    def set_editable(self, editable):
        if self._editable and not editable:
            # Выход из правки: зафиксировать введённое и снова замаскировать.
            self._secret = self.text_edit.toPlainText()
        self._editable = editable
        super().set_editable(editable)
        self._revealed = False
        self.reveal_btn.setChecked(False)
        self.reveal_btn.setText(self._MASK_SHOW)
        self.reveal_btn.setVisible(not editable)   # в правке текст и так виден
        self._refresh()

class IntervalField(QWidget):
    """Поле «Сменять пароль каждые N дней». Значение 0 = срок не задан (None)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.spin = QSpinBox()
        self.spin.setRange(0, 3650)
        self.spin.setSuffix(" дн.")
        self.spin.setSpecialValueText("не задано")  # отображается при значении 0
        # Чуть шире, чтобы «не задано» не обрезалось (внутренние отступы темы).
        self.spin.setMinimumWidth(150)
        self.spin.setReadOnly(True)
        self.spin.setButtonSymbols(QSpinBox.NoButtons)
        layout.addWidget(self.spin)
        layout.addStretch()

    def set_value(self, days):
        self.spin.setValue(int(days) if days else 0)

    def get_value(self):
        v = self.spin.value()
        return v if v > 0 else None

    def set_editable(self, editable):
        self.spin.setReadOnly(not editable)
        self.spin.setButtonSymbols(QSpinBox.UpDownArrows if editable else QSpinBox.NoButtons)

