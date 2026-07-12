"""Поля финансовой карточки: маскированный номер карты и срок действия.

Контракт совпадает с CopyableField (set_text/get_text/set_editable/copy_signal),
чтобы фабрика FinItemTabs строила их единообразно. Бизнес-логика (Луна, маска,
разбор срока) — только вызовы hranilka.core.fin_domain; UI сам ничего не считает.
"""
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QLineEdit, QPushButton,
                               QLabel, QApplication)
from PySide6.QtCore import Signal

from hranilka.core.fin_domain import (luhn_check, mask_card_number,
                                      parse_expiry, days_until_expiry,
                                      EXPIRY_WARN_DAYS)


def _only_digits(text: str) -> str:
    """Только цифры из строки (для номера карты — без пробелов/дефисов)."""
    return "".join(c for c in (text or "") if c.isdigit())


def _grouped(digits: str) -> str:
    """Разбивка цифр по 4 через пробел («4111 1111 1111 1234»)."""
    return " ".join(digits[i:i + 4] for i in range(0, len(digits), 4))


class MaskedCardNumberField(QWidget):
    """Поле номера карты: в просмотре — маска «**** **** **** 1234» с reveal-
    кнопкой (как у пароля), в правке — ввод с автогруппировкой по 4. get_text и
    копирование отдают номер без пробелов. Рядом — индикатор контрольной суммы
    Луна (только предупреждение)."""

    copy_signal = Signal()

    _MASK_SHOW = "[*]"   # скрыто — нажать, чтобы показать
    _MASK_HIDE = "[A]"   # видно — нажать, чтобы скрыть
    _MAX_DIGITS = 19     # максимум цифр в номере карты (ISO/IEC 7812)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._digits = ""
        self._editable = False
        self._reformatting = False           # защита от рекурсии textChanged

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.input = QLineEdit()
        self.input.setReadOnly(True)
        layout.addWidget(self.input)

        self.reveal_btn = QPushButton(self._MASK_SHOW)
        self.reveal_btn.setFixedWidth(45)
        self.reveal_btn.setCheckable(True)
        self.reveal_btn.setToolTip("Показать / скрыть номер")
        self.reveal_btn.clicked.connect(self._refresh_view)
        layout.addWidget(self.reveal_btn)

        self.luhn_label = QLabel("")
        self.luhn_label.setToolTip("Контрольная сумма Луна")
        layout.addWidget(self.luhn_label)

        self.copy_btn = QPushButton("[КОП]")
        self.copy_btn.setFixedWidth(60)
        self.copy_btn.clicked.connect(self.do_copy)
        layout.addWidget(self.copy_btn)

        self.input.textChanged.connect(self._on_text_changed)

    # ----- Контракт поля -----

    def set_text(self, text):
        self._digits = _only_digits(text)[:self._MAX_DIGITS]
        self._refresh_view()

    def get_text(self):
        return self._digits

    def set_placeholder(self, text):
        self.input.setPlaceholderText(text)

    def set_editable(self, editable):
        self._editable = editable
        self.input.setReadOnly(not editable)
        self.copy_btn.setVisible(not editable)
        # Индикатор Луна — только в правке (в просмотре номер маскирован, проверять
        # контрольную сумму пользователю незачем).
        self.luhn_label.setVisible(editable)
        if editable:
            # В правке номер показываем целиком, reveal-кнопку прячем (как у пароля).
            self.reveal_btn.setChecked(False)
            self.reveal_btn.setVisible(False)
        else:
            self.reveal_btn.setVisible(True)
        self._refresh_view()

    def do_copy(self):
        if self._digits:
            QApplication.clipboard().setText(self._digits)   # без пробелов
            self.copy_signal.emit()

    # ----- Внутреннее -----

    def _refresh_view(self):
        """Перерисовать отображение под текущий режим (правка/просмотр/reveal)."""
        self._reformatting = True
        if self._editable or self.reveal_btn.isChecked():
            self.input.setText(_grouped(self._digits))
        else:
            self.input.setText(mask_card_number(self._digits))
        self._reformatting = False
        self.reveal_btn.setText(
            self._MASK_HIDE if self.reveal_btn.isChecked() else self._MASK_SHOW)
        self._update_luhn()

    def _on_text_changed(self, _text):
        if self._reformatting:
            return                           # программная перерисовка — не трогаем
        # Ручной ввод в режиме правки: снимаем цифры, перегруппировываем.
        self._reformatting = True
        self._digits = _only_digits(self.input.text())[:self._MAX_DIGITS]
        grouped = _grouped(self._digits)
        self.input.setText(grouped)
        self.input.setCursorPosition(len(grouped))
        self._reformatting = False
        self._update_luhn()

    def _update_luhn(self):
        if not self._digits:
            self.luhn_label.setText("")
        elif luhn_check(self._digits):
            self.luhn_label.setText("OK")
        else:
            self.luhn_label.setText("[!] контр. сумма")


class ExpiryField(QWidget):
    """Поле срока действия MM/YY: маска ввода, в просмотре — предупреждение,
    если карта истекла/скоро истекает (по days_until_expiry и EXPIRY_WARN_DAYS)."""

    copy_signal = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editable = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.input = QLineEdit()
        # «0» — необязательная цифра (как в CopyableDateField): допускает пустое поле.
        self.input.setInputMask("00/00")
        self.input.setToolTip("Формат: MM/YY")
        self.input.setReadOnly(True)
        layout.addWidget(self.input)

        self.warn_label = QLabel("")
        layout.addWidget(self.warn_label)
        # Пустая метка всё равно оставляет интервалы QHBoxLayout с обеих сторон.
        # Скрываем её до появления предупреждения, чтобы [КОП] шла сразу за
        # полем срока действия с обычным межэлементным отступом.
        self.warn_label.hide()

        self.copy_btn = QPushButton("[КОП]")
        self.copy_btn.setFixedWidth(60)
        self.copy_btn.clicked.connect(self.do_copy)
        layout.addWidget(self.copy_btn)

        self.input.textChanged.connect(self._update_warn)

    # ----- Контракт поля -----

    def set_text(self, text):
        self.input.setText(text or "")
        self._update_warn()

    def get_text(self):
        raw = self.input.text()
        return raw if any(c.isdigit() for c in raw) else ""

    def set_placeholder(self, text):
        self.input.setPlaceholderText(text)

    def set_editable(self, editable):
        self._editable = editable
        self.input.setReadOnly(not editable)
        self.copy_btn.setVisible(not editable)
        self._update_warn()

    def do_copy(self):
        text = self.get_text()
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()

    # ----- Внутреннее -----

    def _update_warn(self):
        # Предупреждение только в просмотре: в правке пользователь ещё печатает.
        if self._editable:
            self.warn_label.setText("")
            self.warn_label.hide()
            return
        raw = self.input.text()
        exp = parse_expiry(raw) if any(c.isdigit() for c in raw) else None
        days = days_until_expiry(exp) if exp else None
        if days is None:
            self.warn_label.setText("")
        elif days < 0:
            self.warn_label.setText("[!] истекла")
        elif days <= EXPIRY_WARN_DAYS:
            self.warn_label.setText("[!] истекает")
        else:
            self.warn_label.setText("")
        self.warn_label.setVisible(bool(self.warn_label.text()))
