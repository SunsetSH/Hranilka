"""KeyValueListWidget — генерик повторяемого блока полей (ListSpec из
core/fin_types.py): адреса кошелька, приватные ключи и будущие списки.

По мотивам SecretQuestionsWidget/CodeListWidget: строки с полями из
item_fields (text → QLineEdit, enum → редактируемый QComboBox, secret →
эхо-режим пароля + reveal [*]), кнопки [+ ДОБАВИТЬ] / [X] на строку,
[КОП] копирует «значение» строки (секретное поле, иначе первое содержательное)
через copy_signal — автоочистку буфера подхватывает MainWindow.

Контракт: get_items() -> list[dict], set_items(list[dict]), set_editable.
"""
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit,
                               QComboBox, QPushButton, QLabel, QApplication)
from PySide6.QtCore import Signal

from hranilka.core.fin_types import FieldSpec
from hranilka.ui.widgets.common import _confirm, ReadOnlyAwareComboBox

# Ретро-маркеры reveal-кнопки секретного поля (как у CopyableField).
_MASK_SHOW = "[*]"
_MASK_HIDE = "[A]"


def _pick_copy_key(item_fields: tuple[FieldSpec, ...]) -> str:
    """Ключ «значения» строки для [КОП]: секретное поле (ключ/пароль), иначе
    первое текстовое не-«label» поле (адрес), иначе первое поле."""
    for f in item_fields:
        if f.secret:
            return f.key
    for f in item_fields:
        if f.kind == "text" and f.key != "label":
            return f.key
    return item_fields[0].key


class _Row:
    """Одна строка списка: виджеты полей по ключам + кнопки строки."""

    def __init__(self, widget: QWidget, fields: dict[str, QWidget],
                 secrets: list[tuple[QLineEdit, QPushButton]],
                 copy_btn: QPushButton, del_btn: QPushButton) -> None:
        self.widget = widget
        self.fields = fields
        self.secrets = secrets          # (поле, reveal-кнопка) секретных полей
        self.copy_btn = copy_btn
        self.del_btn = del_btn


class KeyValueListWidget(QWidget):
    """Список однотипных строк, описанных кортежем FieldSpec (ListSpec)."""

    copy_signal = Signal()

    def __init__(self, item_fields: tuple[FieldSpec, ...], config=None,
                 parent=None):
        super().__init__(parent)
        self.config = config
        self.item_fields = item_fields
        # Ключи «text-полей» строки (kind != "enum"): по ним определяется
        # пустота строки. enum-поля (например, network="BTC" по умолчанию) в
        # проверке пустоты не участвуют — иначе строка с одним лишь дефолтным
        # enum и пустыми текстами ошибочно сохранялась бы.
        self._text_keys = tuple(f.key for f in item_fields if f.kind != "enum")
        self._copy_key = _pick_copy_key(item_fields)
        self.rows: list[_Row] = []
        self._editable = False

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(5)

        self.add_btn = QPushButton("+ ДОБАВИТЬ")
        self.add_btn.clicked.connect(lambda: self.add_row())
        self.layout.addWidget(self.add_btn)
        self.empty_label = QLabel("(список пуст)")
        self.layout.addWidget(self.empty_label)
        self.layout.addStretch()
        self._update_empty()

    # ----- Контракт -----

    def get_items(self) -> list[dict]:
        """Строки со значениями по ключам item_fields. Строка не сохраняется,
        если пусты все её text-поля (enum-значения не считаются содержимым —
        см. _text_keys). Если у типа вообще нет text-полей — fallback к прежнему
        правилу «любое поле непусто»."""
        keys = self._text_keys or tuple(f.key for f in self.item_fields)
        items = []
        for row in self.rows:
            values = {key: self._value(w) for key, w in row.fields.items()}
            if any(values.get(k, "").strip() for k in keys):
                items.append(values)
        return items

    def set_items(self, items: list[dict]) -> None:
        for row in self.rows:
            self.layout.removeWidget(row.widget)
            row.widget.deleteLater()
        self.rows.clear()
        for item in items:
            self.add_row(item)
        self._update_empty()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self.add_btn.setVisible(editable)
        for row in self.rows:
            self._apply_row_mode(row)

    # ----- Строки -----

    def add_row(self, item: dict | None = None) -> None:
        item = item or {}
        row_widget = QWidget(self)
        h = QHBoxLayout(row_widget)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)

        fields: dict[str, QWidget] = {}
        secrets: list[tuple[QLineEdit, QPushButton]] = []
        for spec in self.item_fields:
            w = self._make_field(spec, str(item.get(spec.key, "") or ""))
            fields[spec.key] = w
            h.addWidget(w, 1)
            if spec.secret and isinstance(w, QLineEdit):
                secrets.append((w, self._add_reveal_btn(h, w)))

        copy_btn = QPushButton("[КОП]", row_widget)
        copy_btn.setFixedWidth(60)
        h.addWidget(copy_btn)
        del_btn = QPushButton("[X]", row_widget)
        del_btn.setFixedWidth(40)
        del_btn.clicked.connect(lambda: self.remove_row(row_widget))
        h.addWidget(del_btn)

        row = _Row(row_widget, fields, secrets, copy_btn, del_btn)
        copy_btn.clicked.connect(lambda checked=False, r=row: self._copy_row(r))
        # Строки — перед empty_label и stretch (как в SecretQuestionsWidget).
        self.layout.insertWidget(self.layout.count() - 2, row_widget)
        self.rows.append(row)
        self._apply_row_mode(row)
        self._update_empty()

    def remove_row(self, row_widget: QWidget) -> None:
        if not _confirm(self.config, self, "Удаление", "Удалить эту строку?"):
            return
        for i, row in enumerate(self.rows):
            if row.widget is row_widget:
                self.rows.pop(i)
                break
        self.layout.removeWidget(row_widget)
        row_widget.deleteLater()
        self._update_empty()

    # ----- Внутреннее -----

    def _make_field(self, spec: FieldSpec, value: str) -> QWidget:
        """text/secret → QLineEdit, enum → редактируемый QComboBox."""
        if spec.kind == "enum":
            combo = ReadOnlyAwareComboBox()
            combo.setEditable(True)             # ручной ввод сети вне списка
            combo.addItems([""] + list(spec.options))
            combo.setCurrentText(value)
            combo.lineEdit().setPlaceholderText(spec.placeholder or spec.label)
            return combo
        edit = QLineEdit(value)
        edit.setPlaceholderText(spec.placeholder or spec.label)
        if spec.secret:
            edit.setEchoMode(QLineEdit.Password)
        return edit

    def _add_reveal_btn(self, layout: QHBoxLayout,
                        edit: QLineEdit) -> QPushButton:
        btn = QPushButton(_MASK_SHOW)
        btn.setFixedWidth(45)
        btn.setCheckable(True)
        btn.setToolTip("Показать / скрыть значение")
        btn.clicked.connect(lambda checked, e=edit, b=btn:
                            self._toggle_reveal(e, b))
        layout.addWidget(btn)
        return btn

    @staticmethod
    def _toggle_reveal(edit: QLineEdit, btn: QPushButton) -> None:
        shown = btn.isChecked()
        edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        btn.setText(_MASK_HIDE if shown else _MASK_SHOW)

    @staticmethod
    def _value(widget: QWidget) -> str:
        if isinstance(widget, QComboBox):
            return widget.currentText().strip()
        return widget.text()

    def _copy_row(self, row: _Row) -> None:
        """Скопировать «значение» строки (адрес/ключ) через общий канал."""
        text = self._value(row.fields[self._copy_key])
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()

    def _apply_row_mode(self, row: _Row) -> None:
        """Режим строки: правка — поля активны, секреты видны, reveal скрыт;
        просмотр — read-only, секреты замаскированы, копирование доступно."""
        editable = self._editable
        for w in row.fields.values():
            if isinstance(w, ReadOnlyAwareComboBox):
                # Просмотр: вид как у readonly-поля (не приглушён), клик/колёсико
                # игнорируются — значение случайно не меняется. Без setEnabled(False).
                w.set_view_only(not editable)
            else:
                w.setReadOnly(not editable)
        for edit, btn in row.secrets:
            if editable:
                edit.setEchoMode(QLineEdit.Normal)
                btn.setVisible(False)
            else:
                edit.setEchoMode(QLineEdit.Password)
                btn.setChecked(False)
                btn.setText(_MASK_SHOW)
                btn.setVisible(True)
        row.copy_btn.setVisible(not editable)
        row.del_btn.setVisible(editable)

    def _update_empty(self) -> None:
        self.empty_label.setVisible(not self.rows)
