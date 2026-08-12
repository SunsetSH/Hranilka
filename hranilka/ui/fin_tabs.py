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
import datetime as dt
from typing import Iterator

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QApplication, QFrame, QScrollArea)
from PySide6.QtCore import Signal

from hranilka.core.fin_domain import detect_payment_system
from hranilka.core.fin_types import ItemTypeSpec
from hranilka.ui.flowlayout import WrappingTabWidget
from hranilka.ui.tabs import wrap_scrollable, apply_scroll_areas_bg
from hranilka.ui.widgets import (CopyableField, CopyableDateField,
                                 CopyableTextEdit, GalleryWidget,
                                 MaskedCardNumberField, ExpiryField,
                                 KeyValueListWidget, LinkedAccountsWidget,
                                 ReadOnlyAwareComboBox, SeedPhraseWidget,
                                 heading_label)


def _parse_iso_date(text):
    """«ГГГГ-ММ-ДД» → datetime.date или None (пустая/битая строка)."""
    if not text:
        return None
    try:
        return dt.datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


class _DateField(QWidget):
    """Адаптер CopyableDateField к строковому контракту set_text/get_text.
    payload хранит дату строкой «ГГГГ-ММ-ДД» (как AccountData)."""

    copy_signal = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._inner = CopyableDateField(is_datetime=False)
        self._inner.copy_signal.connect(self.copy_signal.emit)
        layout.addWidget(self._inner)

    def set_text(self, text):
        self._inner.set_date(_parse_iso_date(text))

    def get_text(self):
        value = self._inner.get_date()          # datetime или None
        return value.date().isoformat() if value else ""

    def set_placeholder(self, text):
        pass                                    # у поля даты своя маска-подсказка

    def set_editable(self, editable):
        self._inner.set_editable(editable)


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


class FinItemTabs(WrappingTabWidget):
    """Карточка финансовой записи, собранная из ItemTypeSpec."""

    def __init__(self, spec: ItemTypeSpec, parent=None, config=None):
        super().__init__(parent)
        self.spec = spec
        self.config = config
        self._widgets: dict[str, QWidget] = {}      # key поля → виджет
        self._list_widgets: dict[str, KeyValueListWidget] = {}  # key ListSpec
        # Автодетект платёжной системы по BIN. Флаги: override — пользователь
        # задал систему вручную (не перетираем); suppress — идёт программная
        # массовая установка (load_payload/сам автодетект), сигналы игнорируем.
        self._payment_system_overridden = False
        self._suppress_ps_signal = False
        self._scroll_areas: list[QScrollArea] = []
        self.build_tabs()
        self._wire_bin_autodetect()

    def _add_scroll_tab(self, inner: QWidget, title: str) -> None:
        sa = wrap_scrollable(inner, self)
        self._scroll_areas.append(sa)
        index = self.addTab(sa, title)
        self.bind_empty_page(inner, index)

    def build_tabs(self):
        for tab_name in self.spec.tabs:
            self._add_scroll_tab(self._build_tab(tab_name), tab_name)
        # Вкладка «Галерея» — последняя, общая для всех типов (концепт §5).
        # Отдельной вкладки «Связи» нет: связи показаны внизу первой вкладки.
        self._add_scroll_tab(self._build_gallery_tab(), "Галерея")

    def apply_scroll_bg(self, main_bg: str) -> None:
        """Обновить фон прокручиваемых областей вкладок под цвет из настроек
        (как AccountTabs.apply_scroll_bg — общий хелпер, ui/tabs.py)."""
        apply_scroll_areas_bg(self._scroll_areas, main_bg)

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

    def _add_links_section(self, page, layout):
        """Секция «ИСПОЛЬЗУЕТСЯ В АККАУНТАХ» внизу первой вкладки (у всех типов,
        концепт §8): отделяется горизонтальной линией. Отдельной вкладки нет."""
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)
        self.f_linked_accounts = LinkedAccountsWidget()
        label = heading_label("ИСПОЛЬЗУЕТСЯ В АККАУНТАХ:")
        layout.addWidget(label)
        layout.addWidget(self.f_linked_accounts)
        self.register_empty_section(
            page, (line, label, self.f_linked_accounts),
            lambda: bool(self.f_linked_accounts.get_data()))

    def _build_gallery_tab(self):
        """Вкладка «Галерея»: переиспользованный GalleryWidget аккаунта (§5).
        Загрузка/сохранение BLOB и ленивый предпросмотр подключаются извне
        (FinCardMixin), контракт H-6/M7-03 сохраняется без правок gallery.py."""
        w = QWidget(self)
        layout = QVBoxLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 8, 0, 0)
        self.f_gallery_widget = GalleryWidget(config=self.config)
        layout.addWidget(self.f_gallery_widget)
        self.register_empty_section(
            w, self.f_gallery_widget,
            lambda: bool(self.f_gallery_widget.items))
        return w

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
            return _DateField()
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

    # ----- Итерация по дескриптору -----

    def fields(self) -> Iterator[tuple[str, QWidget]]:
        """(key, widget) для каждого поля типа в порядке дескриптора."""
        for field in self.spec.fields:
            yield field.key, self._widgets[field.key]

    def list_fields(self) -> Iterator[tuple[str, KeyValueListWidget]]:
        """(key, widget) для каждого повторяемого блока (ListSpec)."""
        for ls in self.spec.lists:
            yield ls.key, self._list_widgets[ls.key]

    def set_all_editable(self, editable: bool) -> None:
        self.f_name.set_editable(editable)
        for _key, widget in self.fields():
            widget.set_editable(editable)
        for _key, widget in self.list_fields():
            widget.set_editable(editable)
        self.f_linked_accounts.set_editable(editable)
        self.f_gallery_widget.set_editable(editable)
        self.refresh_empty_visibility(editable)

    def load_payload(self, payload: dict) -> None:
        """Заполнить поля из payload. Программная установка — не триггерит
        автодетект/override платёжной системы."""
        self._suppress_ps_signal = True
        for key, widget in self.fields():
            widget.set_text(str(payload.get(key, "") or ""))
        self._suppress_ps_signal = False
        for key, list_widget in self.list_fields():
            value = payload.get(key)
            # Толерантное чтение: не-список (битые данные) — пустой блок.
            list_widget.set_items(value if isinstance(value, list) else [])
        # Уже сохранённую систему считаем ручной — автодетект её не перетирает.
        self._payment_system_overridden = bool(payload.get("payment_system"))

    def collect_payload(self) -> dict:
        payload = {key: widget.get_text() for key, widget in self.fields()}
        payload.update(
            {key: widget.get_items() for key, widget in self.list_fields()})
        return payload

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
