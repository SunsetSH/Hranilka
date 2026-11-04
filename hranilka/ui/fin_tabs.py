"""FinItemTabs — вкладки и поля финансовой карточки, построенные ИЗ дескриптора
ItemTypeSpec (core/fin_types.py). Ни ручных списков полей, ни ручной сериализации:
всё итерацией по spec.fields. Добавление поля типа = одна строка в дескрипторе.

Фабрика kind → виджет: text→CopyableField (secret→is_password), multiline→
CopyableTextEdit, date→адаптер вокруг CopyableDateField, enum→редактируемый
QComboBox, int/money→CopyableField, seed→SeedPhraseWidget; ListSpec→
KeyValueListWidget; спец-поля по ключу: card_number→MaskedCardNumberField,
expiry→ExpiryField. Поля с общим FieldSpec.row_group рендерятся в одну строку.
Автодетект платёжной системы по BIN. Первое поле первой вкладки каждого типа —
имя записи (f_name); связи с аккаунтами (концепт §8) — внизу первой вкладки;
последней добавляется общая вкладка «Галерея».
"""
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QApplication)
from PySide6.QtCore import Signal

from hranilka.core.fin_domain import detect_payment_system
from hranilka.core.fin_types import ItemTypeSpec
from hranilka.ui.tabs import PayloadTabs
from hranilka.ui.widgets.fields import CopyableIsoDateField
from hranilka.ui.widgets import (CopyableField,
                                 CopyableTextEdit, MaskedCardNumberField, ExpiryField,
                                 KeyValueListWidget, ReadOnlyAwareComboBox, SeedPhraseWidget,
                                 heading_label)


class _EnumField(QWidget):
    """Редактируемый QComboBox под контракт поля (set_text/get_text/set_editable/
    copy_signal). Варианты из дескриптора + пустой первый пункт; ручной ввод
    допускается (валюта/платёжная система вне списка)."""

    copy_signal = Signal()

    def __init__(self, options, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.combo = ReadOnlyAwareComboBox()
        self.combo.setEditable(True)
        self.combo.addItems([""] + list(options))
        layout.addWidget(self.combo, 1)

        self.copy_btn = QPushButton("[КОП]")
        self.copy_btn.setFixedWidth(60)
        self.copy_btn.clicked.connect(self.do_copy)
        layout.addWidget(self.copy_btn)

    def set_text(self, text):
        self.combo.setCurrentText(text or "")

    def get_text(self):
        return self.combo.currentText().strip()

    def set_placeholder(self, text):
        self.combo.lineEdit().setPlaceholderText(text)

    def set_editable(self, editable):
        # В просмотре комбо только для чтения, НО без setEnabled(False): вид как у
        # readonly-поля (текст не приглушён), клик/колёсико игнорируются; копия — рядом.
        self.combo.set_view_only(not editable)
        self.copy_btn.setVisible(not editable)

    def do_copy(self):
        text = self.get_text()
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()


class FinItemTabs(PayloadTabs):
    LINKS_HEADING = "ИСПОЛЬЗУЕТСЯ В АККАУНТАХ:"
    LINKS_SEPARATOR = True

    """Карточка финансовой записи, собранная из ItemTypeSpec."""

    def __init__(self, spec: ItemTypeSpec, parent=None, config=None):
        super().__init__(parent, config)
        self.spec = spec
        # Автодетект платёжной системы по BIN. Флаги: override — пользователь
        # задал систему вручную (не перетираем); suppress — идёт программная
        # массовая установка (load_payload/сам автодетект), сигналы игнорируем.
        self._payment_system_overridden = False
        self._suppress_ps_signal = False
        self.build_tabs()
        self._wire_bin_autodetect()

    def build_tabs(self):
        for tab_name in self.spec.tabs:
            self._add_scroll_tab(self._build_tab(tab_name), tab_name)
        # Вкладка «Галерея» — последняя, общая для всех типов (концепт §5).
        # Отдельной вкладки «Связи» нет: связи показаны внизу первой вкладки.
        self._add_scroll_tab(self._build_gallery_tab(), "Галерея")

    def _build_tab(self, tab_name):
        w = QWidget(self)
        layout = QVBoxLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 8, 0, 0)
        first_tab = bool(self.spec.tabs) and tab_name == self.spec.tabs[0]
        if first_tab:
            # Имя записи — первое поле первой вкладки каждого типа (часть storage).
            self.f_name = CopyableField()
            name_label = heading_label("Название (обязательно):")
            layout.addWidget(name_label)
            layout.addWidget(self.f_name)
            self.register_empty_section(
                w, (name_label, self.f_name),
                lambda: bool(self.f_name.get_text().strip()), required=True)
        self._add_field_widgets(w, layout, tab_name)
        # Повторяемые блоки (адреса, приватные ключи) — генерик по ListSpec.
        for ls in self.spec.lists:
            if ls.tab != tab_name:
                continue
            list_widget = KeyValueListWidget(ls.item_fields, config=self.config)
            self._list_widgets[ls.key] = list_widget
            list_label = heading_label(ls.label + ":")
            layout.addWidget(list_label)
            layout.addWidget(list_widget)
            self.register_empty_section(
                w, (list_label, list_widget),
                lambda lw=list_widget: bool(lw.get_items()))
        if first_tab:
            self._add_links_section(w, layout)
        layout.addStretch()
        return w

    def _add_field_widgets(self, page, layout, tab_name):
        """Разложить поля вкладки: подряд идущие поля с общим row_group — в одну
        строку (generic для любых будущих групп), остальные — колонкой."""
        fields = [f for f in self.spec.fields if f.tab == tab_name]
        i = 0
        while i < len(fields):
            field = fields[i]
            if field.row_group is None:
                widget = self._make_widget(field)
                self._widgets[field.key] = widget
                label = heading_label(field.label + ":")
                layout.addWidget(label)
                layout.addWidget(widget)
                self.register_empty_section(
                    page, (label, widget),
                    lambda w=widget: bool(w.get_text().strip()))
                i += 1
                continue
            # Собираем подряд идущие поля одной группы в общую строку.
            group = [field]
            j = i + 1
            while j < len(fields) and fields[j].row_group == field.row_group:
                group.append(fields[j])
                j += 1
            layout.addLayout(self._build_field_row(page, group))
            i = j

    def _build_field_row(self, page, group):
        """Строка из мини-колонок (подпись над полем), делящих ширину поровну."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        for field in group:
            column_index = row.count()
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(4)                # подпись плотно над своим полем
            widget = self._make_widget(field)
            self._widgets[field.key] = widget
            label = heading_label(field.label + ":")
            col.addWidget(label)
            col.addWidget(widget)
            row.addLayout(col, 1)            # равная доля ширины
            self.register_empty_section(
                page, (label, widget),
                lambda w=widget: bool(w.get_text().strip()),
                visibility_changed=lambda shown, r=row, i=column_index:
                    r.setStretch(i, 1 if shown else 0))
        return row

    def _make_widget(self, field):
        """kind/ключ → конкретный виджет (единая точка фабрики)."""
        if field.key == "card_number":
            return MaskedCardNumberField()
        if field.key == "expiry":
            widget = ExpiryField()
            if field.placeholder:
                widget.set_placeholder(field.placeholder)
            return widget
        if field.kind == "seed":
            return SeedPhraseWidget()
        if field.kind == "multiline":
            return CopyableTextEdit()
        if field.kind == "date":
            return CopyableIsoDateField()
        if field.kind == "enum":
            widget = _EnumField(field.options)
            if field.placeholder:
                widget.set_placeholder(field.placeholder)
            return widget
        # text / int / money — обычное копируемое поле (secret → пароль-режим).
        widget = CopyableField(is_password=field.secret)
        if field.placeholder:
            widget.set_placeholder(field.placeholder)
        return widget

    # ----- Итерация по дескриптору (fields/list_fields - в PayloadTabs) -----

    def _field_keys(self):
        return (f.key for f in self.spec.fields)

    def _list_keys(self):
        return (ls.key for ls in self.spec.lists)

    def _load_fields(self, payload: dict) -> None:
        """Программная установка - не триггерит автодетект/override системы."""
        self._suppress_ps_signal = True
        try:
            super()._load_fields(payload)
        finally:
            self._suppress_ps_signal = False

    def _load_extra(self, payload: dict) -> None:
        # Уже сохранённую систему считаем ручной - автодетект её не перетирает.
        self._payment_system_overridden = bool(payload.get("payment_system"))

    # ----- Автодетект платёжной системы по BIN -----

    def _wire_bin_autodetect(self):
        card = self._widgets.get("card_number")
        ps = self._widgets.get("payment_system")
        if card is None or ps is None:
            return
        card.input.textChanged.connect(self._on_card_number_changed)
        ps.combo.currentTextChanged.connect(self._on_ps_changed)

    def _on_card_number_changed(self, _text):
        if self._suppress_ps_signal or self._payment_system_overridden:
            return
        system = detect_payment_system(self._widgets["card_number"].get_text())
        if system:
            self._suppress_ps_signal = True
            self._widgets["payment_system"].set_text(system)
            self._suppress_ps_signal = False

    def _on_ps_changed(self, _text):
        # Ручное изменение системы фиксирует override (автодетект больше не трогает).
        if not self._suppress_ps_signal:
            self._payment_system_overridden = True
