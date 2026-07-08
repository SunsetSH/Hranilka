"""Поля карточки аккаунта: копируемые строка/дата/текст и интервал в днях
(вынесены из widgets.py, этап 5)."""
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton, QTextEdit, QApplication, QSpinBox)
from PySide6.QtCore import Signal, Qt, QDate, QDateTime, QTime


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
        self.format = "dd.MM.yyyy HH:mm" if is_datetime else "dd.MM.yyyy"

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
        dt = self.get_date()
        if dt is None:
            return
        QApplication.clipboard().setText(dt.toString(self.format))
        self.copy_signal.emit()

    def set_date(self, date):
        """Принимает QDate, QDateTime, None или строку. None/пусто → «не задано»."""
        if isinstance(date, QDateTime):
            self.date_widget.setText(date.toString(self.format))
        elif isinstance(date, QDate):
            if self._is_datetime:
                self.date_widget.setText(
                    QDateTime(date, QTime(0, 0)).toString(self.format))
            else:
                self.date_widget.setText(date.toString(self.format))
        else:
            # None или неизвестный формат — «не задано», а не «сегодня».
            self.date_widget.setText("")

    def get_date(self):
        """QDateTime или None, если дата не задана/не дописана.

        Для is_datetime незаполненное время считается 00:00 — дата без
        времени не должна пропадать при сохранении."""
        raw = self.date_widget.text()          # маска: пусто = "..[ :]"
        date_part = raw[:10].strip(" .")
        if not date_part:
            return None
        d = QDate.fromString(raw[:10], "dd.MM.yyyy")
        if not d.isValid():
            return None
        t = QTime(0, 0)
        if self._is_datetime:
            parsed = QTime.fromString(raw[11:16], "HH:mm")
            if parsed.isValid():
                t = parsed
        return QDateTime(d, t)

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
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.copy_btn = QPushButton("[КОП]")
        self.copy_btn.setFixedWidth(60)
        self.copy_btn.clicked.connect(self.do_copy)
        btn_layout.addWidget(self.copy_btn)
        layout.addLayout(btn_layout)
        
    def do_copy(self):
        text = self.text_edit.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()
            
    def set_text(self, text): self.text_edit.setPlainText(text)
    def get_text(self): return self.text_edit.toPlainText()
    def set_editable(self, editable):
        self.text_edit.setReadOnly(not editable)
        self.copy_btn.setVisible(not editable)

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

