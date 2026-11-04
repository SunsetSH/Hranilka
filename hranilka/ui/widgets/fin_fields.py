"""Поля финансовой карточки: маскированный номер карты и срок действия.

Контракт совпадает с CopyableField (set_text/get_text/set_editable/copy_signal),
чтобы фабрика FinItemTabs строила их единообразно. Бизнес-логика (Луна, маска,
разбор срока) — только вызовы hranilka.core.fin_domain; UI сам ничего не считает.
"""
import re

from PySide6.QtWidgets import (QWidget, QHBoxLayout, QLineEdit, QPushButton,
                               QLabel)
from PySide6.QtCore import Signal

from hranilka.ui.widgets.common import copy_secret, make_copy_button, set_a11y

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

    _MASK_SHOW = "*"   # скрыто — нажать, чтобы показать
    _MASK_HIDE = "A"   # видно — нажать, чтобы скрыть
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
        set_a11y(self.reveal_btn, "Показать или скрыть номер карты")
        self.reveal_btn.clicked.connect(self._refresh_view)
        layout.addWidget(self.reveal_btn)

        self.luhn_label = QLabel("")
        self.luhn_label.setToolTip("Контрольная сумма Луна")
        layout.addWidget(self.luhn_label)

        self.copy_btn = make_copy_button(name="Копировать номер карты")
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
            copy_secret(self._digits)   # без пробелов
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


_ISO_MONTH = re.compile(r"\s*(\d{4})-(\d{1,2})(?:-\d{1,2})?\s*")


def normalize_expiry(text):
    """Срок в любом из поддерживаемых форматов (MM/YY, M/YYYY, YYYY-MM,
    YYYY-MM-DD) → «MM/YY»; None — не распознан или месяц вне 1..12."""
    text = (text or "").strip()
    m = _ISO_MONTH.fullmatch(text)
    if m:
        text = f"{int(m.group(2))}/{m.group(1)}"
    exp = parse_expiry(text)
    return f"{exp.month:02d}/{exp.year % 100:02d}" if exp else None


class ExpiryField(QWidget):
    """Поле срока действия MM/YY: маска ввода, в просмотре — предупреждение,
    если карта истекла/скоро истекает (по days_until_expiry и EXPIRY_WARN_DAYS)."""

    copy_signal = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editable = False
        self._original = ""        # значение, пришедшее из set_text (как хранится)
        self._edited = False       # пользователь правил поле руками

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

        self.copy_btn = make_copy_button(name="Копировать срок действия")
        self.copy_btn.clicked.connect(self.do_copy)
        layout.addWidget(self.copy_btn)

        self.input.textChanged.connect(self._update_warn)
        self.input.textEdited.connect(self._on_user_edit)

    # ----- Контракт поля -----

    def set_text(self, text):
        self._original = text or ""
        self._edited = False
        # Прежние форматы (YYYY-MM, MM/YYYY, M/YY…) приводятся к MM/YY, иначе
        # маска «00/00» исказила бы их (например «12/2027» → «12/20»).
        self.input.setText(normalize_expiry(self._original) or "")
        self._update_warn()

    def get_text(self):
        """Не тронутое пользователем поле возвращает исходное значение как есть.
        После правки — валидный срок в виде MM/YY; полностью очищенное поле —
        ""; мусор/недописанный/несуществующий месяц НЕ затирает прежнее
        значение (возвращается исходное)."""
        if not self._edited:
            # Прежний формат (YYYY-MM…) приводим к MM/YY: parse_expiry понимает
            # только MM/YY и MM/YYYY, иначе expires_on стал бы None при
            # предупреждающем UI. Нераспознанное отдаём как есть.
            return normalize_expiry(self._original) or self._original
        raw = self.input.text()
        if not any(c.isdigit() for c in raw):
            return ""
        return normalize_expiry(raw) or self._original

    def _on_user_edit(self, _text):
        self._edited = True
        self._update_warn()          # подсказка зависит от _edited

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
            copy_secret(text)
            self.copy_signal.emit()

    # ----- Внутреннее -----

    def _update_warn(self):
        # Предупреждение только в просмотре: в правке пользователь ещё печатает.
        raw = self.input.text()
        if self._editable:
            bad = any(c.isdigit() for c in raw) and parse_expiry(raw) is None
            keep = bool(self._original) and self._edited
            self.warn_label.setText(
                ("[!] MM/YY, сохранится прежнее значение" if keep
                 else "[!] MM/YY") if bad else "")
            self.warn_label.setVisible(bad)
            return
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
