"""KeyValueListWidget — генерик повторяемого блока полей (ListSpec из
core/fin_types.py): адреса кошелька, приватные ключи и будущие списки.

По мотивам SecretQuestionsWidget/CodeListWidget: строки с полями из
item_fields (text → QLineEdit, enum → редактируемый QComboBox, secret →
эхо-режим пароля + reveal [*], multiline → CopyableTextEdit/MaskedTextEdit —
большие значения вроде PEM-ключей рендерятся под однострочными полями на всю
ширину строки), кнопки [+ ДОБАВИТЬ] / [X] на строку,
[КОП] копирует «значение» строки (секретное поле, иначе первое содержательное)
через copy_signal — автоочистку буфера подхватывает MainWindow.

Контракт: get_items() -> list[dict], set_items(list[dict]), set_editable.

Аддитивно (УИ §2026-07-15): FieldSpec.copy_btn — своя [КОП] у конкретного
поля строки (в дополнение к общей [КОП] строки, нужна для полей вне первого
row_group). Если поле-«значение» строки САМО оказалось в первом row_group-
ряду со своей [КОП] (УИ §2026-07-16, третий заход) — общая [КОП] строки была
бы визуальным дублем прямо в том же ряду; _suppress_general_copy её не
рендерит (_Row.copy_btn -> None), [X] при этом остаётся всегда.

FieldSpec.gen_btn (УИ §2026-07-16, как в карточке аккаунта — ui/tabs.py):
поле получает пару кнопок ПОД полями строки (во всю ширину, равной ширины) —
«СГЕНЕРИРОВАТЬ ПАРОЛЬ» (gen_callback) и «ПАРАМЕТРЫ ГЕНЕРАЦИИ»
(gen_settings_callback, открывает диалог настроек и возвращает пароль
предпросмотра для подстановки — как AccountCardMixin.
open_password_generator_settings). kv_list не импортирует генератор — только
зовёт переданные callable, поэтому не знает про password_gen/
GeneratorSettingsDialog (их передаёт ServerTabs, см. ui/server_tabs.py)."""
from typing import Callable

from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit,
                               QComboBox, QPushButton, QLabel, QApplication)
from PySide6.QtCore import Signal

from hranilka.core.fin_types import FieldSpec
from hranilka.ui.widgets.common import _confirm, ReadOnlyAwareComboBox, heading_label
from hranilka.ui.widgets.fields import CopyableTextEdit, MaskedTextEdit

# Ретро-маркеры reveal-кнопки секретного поля (как у CopyableField).
_MASK_SHOW = "[*]"
_MASK_HIDE = "[A]"

# Доп. нижний отступ многорядной строки списка (несколько row_group-рядов
# и/или multiline-блок) поверх обычного layout.setSpacing(5) — визуально
# отделяет соседние строки (панели и т.п., УИ §2026-07-15). Однорядные строки
# этот отступ не получают (0) — межстрочный интервал не меняется.
_MULTI_ROW_EXTRA_GAP = 10


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
                 copy_btn: QPushButton | None, del_btn: QPushButton,
                 field_copy_btns: dict[str, QPushButton] | None = None,
                 field_gen_btns: dict[str, QPushButton] | None = None,
                 field_gen_settings_btns: dict[str, QPushButton] | None = None
                 ) -> None:
        self.widget = widget
        self.fields = fields
        self.secrets = secrets          # (поле, reveal-кнопка) секретных полей
        # copy_btn — None, когда общая [КОП] строки подавлена (см.
        # _suppress_general_copy): поле-«значение» строки уже находится в
        # первом ряду СО своей индивидуальной [КОП] — вторая кнопка рядом
        # была бы буквальным визуальным дублем (пользователи ОС, УИ
        # §2026-07-XX). del_btn при этом остаётся всегда.
        self.copy_btn = copy_btn
        self.del_btn = del_btn
        # Индивидуальные [КОП]/генерация полей (FieldSpec.copy_btn/gen_btn) —
        # ключ поля -> кнопка. Пустые словари по умолчанию (большинство строк
        # их не использует). field_gen_btns — «СГЕНЕРИРОВАТЬ ПАРОЛЬ»,
        # field_gen_settings_btns — «ПАРАМЕТРЫ ГЕНЕРАЦИИ» (пара равной ширины
        # под полями строки, УИ §2026-07-16).
        self.field_copy_btns = field_copy_btns or {}
        self.field_gen_btns = field_gen_btns or {}
        self.field_gen_settings_btns = field_gen_settings_btns or {}


class KeyValueListWidget(QWidget):
    """Список однотипных строк, описанных кортежем FieldSpec (ListSpec)."""

    copy_signal = Signal()

    def __init__(self, item_fields: tuple[FieldSpec, ...], config=None,
                 parent=None, copy_key: str | None = None,
                 gen_callback: Callable[[], str] | None = None,
                 gen_settings_callback: Callable[[], str | None] | None = None):
        super().__init__(parent)
        self.config = config
        self.item_fields = item_fields
        # Callable[[], str] для FieldSpec.gen_btn (кнопка «СГЕНЕРИРОВАТЬ
        # ПАРОЛЬ») и Callable[[], str | None] для «ПАРАМЕТРЫ ГЕНЕРАЦИИ»
        # (открывает диалог настроек, возвращает пароль предпросмотра при
        # сохранении, иначе None при отмене) — виджет ничего не знает про
        # генератор паролей, только зовёт переданные функции (ServerTabs).
        self._gen_callback = gen_callback
        self._gen_settings_callback = gen_settings_callback
        # Ключи «text-полей» строки (kind != "enum"): по ним определяется
        # пустота строки. enum-поля (например, network="BTC" по умолчанию) в
        # проверке пустоты не участвуют — иначе строка с одним лишь дефолтным
        # enum и пустыми текстами ошибочно сохранялась бы.
        self._text_keys = tuple(f.key for f in item_fields if f.kind != "enum")
        # Обычно общая кнопка строки копирует секрет (пароль/ключ).  В строке
        # панели она расположена возле URL, поэтому вызывающий код может явно
        # задать ключ ссылки, не меняя поведение остальных списков.
        field_keys = {field.key for field in item_fields}
        if copy_key is not None and copy_key not in field_keys:
            raise ValueError(f"Неизвестное поле для копирования: {copy_key}")
        self._copy_key = copy_key or _pick_copy_key(item_fields)
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
        """Строка рендерится в несколько уровней: однострочные поля — один или
        несколько рядов (группировка по FieldSpec.row_group, [КОП]/[X] — в
        конце первого ряда), ниже — multiline-поля (с подписью) во всю ширину
        строки. Без row_group все однострочные поля собираются в один общий
        ряд — прежнее поведение (панели/пользователи/SSH-ключи не меняются)."""
        item = item or {}
        row_widget = QWidget(self)
        outer = QVBoxLayout(row_widget)
        line_specs = [f for f in self.item_fields if f.kind != "multiline"]
        multiline_specs = [f for f in self.item_fields if f.kind == "multiline"]
        line_rows = self._line_rows(line_specs)
        # Многорядная строка (несколько row_group-рядов и/или multiline-блок
        # снизу) — увеличенный нижний отступ, иначе строки списка визуально
        # сливаются (панели, УИ §2026-07-15). Однорядные списки (пользователи,
        # фин-списки без row_group/multiline) — без изменений (0 = как раньше).
        is_multi_row = len(line_rows) > 1 or bool(multiline_specs)
        outer.setContentsMargins(0, 0, 0, _MULTI_ROW_EXTRA_GAP if is_multi_row else 0)
        outer.setSpacing(5)

        fields: dict[str, QWidget] = {}
        secrets: list[tuple[QLineEdit, QPushButton]] = []
        narrow_widgets: list[tuple[QWidget, FieldSpec]] = []
        field_copy_btns: dict[str, QPushButton] = {}
        # Поля с gen_btn=True — рендерятся ПОСЛЕ всех line_rows (под полями
        # строки, во всю ширину), а не инлайн у поля (УИ §2026-07-16, как в
        # карточке аккаунта).
        gen_targets: list[tuple[FieldSpec, QLineEdit]] = []

        suppress_general_copy = self._suppress_general_copy(line_rows)
        copy_btn: QPushButton | None = None
        del_btn: QPushButton | None = None
        for group_specs, is_first_row in line_rows:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            for spec in group_specs:
                w = self._make_field(spec, str(item.get(spec.key, "") or ""))
                fields[spec.key] = w
                row.addWidget(w, 0 if spec.narrow else 1)
                if spec.narrow:
                    narrow_widgets.append((w, spec))
                if spec.secret and isinstance(w, QLineEdit):
                    secrets.append((w, self._add_reveal_btn(row, w)))
                if spec.gen_btn and isinstance(w, QLineEdit):
                    gen_targets.append((spec, w))
                if spec.copy_btn:
                    field_copy_btns[spec.key] = self._add_field_copy_btn(row, w)
            if is_first_row:
                if not suppress_general_copy:
                    copy_btn = QPushButton("[КОП]", row_widget)
                    copy_btn.setFixedWidth(60)
                    row.addWidget(copy_btn)
                del_btn = QPushButton("[X]", row_widget)
                del_btn.setFixedWidth(40)
                del_btn.clicked.connect(lambda: self.remove_row(row_widget))
                row.addWidget(del_btn)
            outer.addLayout(row)

        field_gen_btns: dict[str, QPushButton] = {}
        field_gen_settings_btns: dict[str, QPushButton] = {}
        for spec, edit in gen_targets:
            gen_btn, cfg_btn = self._add_gen_button_row(outer, edit)
            if gen_btn is not None:
                field_gen_btns[spec.key] = gen_btn
            if cfg_btn is not None:
                field_gen_settings_btns[spec.key] = cfg_btn

        for spec in multiline_specs:
            w = self._make_field(spec, str(item.get(spec.key, "") or ""))
            fields[spec.key] = w
            outer.addWidget(heading_label(spec.label + ":"))
            outer.addWidget(w)

        assert del_btn is not None   # _line_rows всегда даёт хотя бы один (первый) ряд
        row = _Row(row_widget, fields, secrets, copy_btn, del_btn,
                  field_copy_btns, field_gen_btns, field_gen_settings_btns)
        if copy_btn is not None:
            copy_btn.clicked.connect(lambda checked=False, r=row: self._copy_row(r))
        # Строки — перед empty_label и stretch (как в SecretQuestionsWidget).
        self.layout.insertWidget(self.layout.count() - 2, row_widget)
        self.rows.append(row)
        self._apply_row_mode(row)
        self._update_empty()
        # Ширина узких полей — ПОСЛЕ вставки строки в реальное дерево виджетов:
        # пока row_widget не встроен в self.layout, вложенные виджеты ещё не
        # унаследовали тему (шрифт), и fontMetrics() даёт неверный расчёт.
        for widget, spec in narrow_widgets:
            self._apply_narrow_width(widget, spec)

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

    @staticmethod
    def _line_rows(
        line_specs: list[FieldSpec],
    ) -> list[tuple[list[FieldSpec], bool]]:
        """Группирует однострочные поля строки в ряды по FieldSpec.row_group
        (порядок первого появления группы сохраняется); поля без row_group
        (None) делят один общий ряд — прежнее поведение без правок вызовов.
        Возвращает [(поля_ряда, это_первый_ряд), ...] — [КОП]/[X] крепятся к
        первому ряду. Пустой line_specs всё равно даёт один (пустой) ряд —
        кнопкам есть куда встать."""
        groups: dict[object, list[FieldSpec]] = {}
        order: list[object] = []
        for spec in line_specs:
            key = spec.row_group
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(spec)
        if not order:
            order = [None]
            groups[None] = []
        return [(groups[key], i == 0) for i, key in enumerate(order)]

    def _suppress_general_copy(
        self, line_rows: list[tuple[list[FieldSpec], bool]]
    ) -> bool:
        """Общая [КОП] строки не рендерится, если поле-«значение» строки
        (self._copy_key) находится в ПЕРВОМ ряду И само уже несёт
        индивидуальную [КОП] (FieldSpec.copy_btn) — иначе в одном ряду стояли
        бы две одинаковые по смыслу кнопки (пользователи ОС: логин+пароль в
        первом ряду, у пароля copy_btn=True). Если поле-«значение» в другом
        ряду (панели: пароль во втором ряду, общая [КОП] — в первом у URL) —
        общая кнопка НЕ подавляется, визуального дублирования там нет."""
        if not line_rows:
            return False
        first_group = line_rows[0][0]
        for spec in first_group:
            if spec.key == self._copy_key:
                return bool(spec.copy_btn)
        return False

    def _make_field(self, spec: FieldSpec, value: str) -> QWidget:
        """text/secret → QLineEdit, enum → редактируемый QComboBox,
        multiline → CopyableTextEdit (secret → MaskedTextEdit, reveal — из
        коробки самого виджета, как на вкладке «Фраза восстановления»)."""
        if spec.kind == "enum":
            combo = ReadOnlyAwareComboBox()
            combo.setEditable(True)             # ручной ввод сети вне списка
            combo.addItems([""] + list(spec.options))
            combo.setCurrentText(value)
            combo.lineEdit().setPlaceholderText(spec.placeholder or spec.label)
            return combo
        if spec.kind == "multiline":
            text_edit = MaskedTextEdit() if spec.secret else CopyableTextEdit()
            text_edit.set_text(value)
            return text_edit
        edit = QLineEdit(value)
        edit.setPlaceholderText(spec.placeholder or spec.label)
        if spec.secret:
            edit.setEchoMode(QLineEdit.Password)
        return edit

    @staticmethod
    def _apply_narrow_width(widget: QWidget, spec: FieldSpec) -> None:
        """Узкое поле: ширина по тексту-подсказке (fontMetrics) + запас под
        рамку/стрелку комбобокса — вместо растяжения по стрейчу ряда.
        Вызывается уже ПОСЛЕ вставки строки в дерево виджетов (add_row) —
        иначе fontMetrics() ещё не отражает унаследованный шрифт темы."""
        text = spec.placeholder or spec.label
        extra = 40 if spec.kind == "enum" else 24
        width = widget.fontMetrics().horizontalAdvance(text) + extra
        widget.setFixedWidth(width)

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

    def _add_field_copy_btn(self, layout: QHBoxLayout,
                            widget: QWidget) -> QPushButton:
        """[КОП] отдельного поля (FieldSpec.copy_btn) — независимо от общей
        [КОП] строки (та копирует _copy_key и может быть в другом row_group-
        ряду). Тот же канал автоочистки буфера (copy_signal)."""
        btn = QPushButton("[КОП]")
        btn.setFixedWidth(60)
        btn.setToolTip("Скопировать значение поля")
        btn.clicked.connect(lambda checked=False, w=widget: self._copy_field(w))
        layout.addWidget(btn)
        return btn

    def _copy_field(self, widget: QWidget) -> None:
        text = self._value(widget)
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()

    def _add_gen_button_row(
        self, outer: QVBoxLayout, edit: QLineEdit
    ) -> tuple[QPushButton | None, QPushButton | None]:
        """Пара кнопок ПОД полями строки (FieldSpec.gen_btn), равной ширины —
        «СГЕНЕРИРОВАТЬ ПАРОЛЬ» / «ПАРАМЕТРЫ ГЕНЕРАЦИИ», как в карточке
        аккаунта (ui/tabs.py: create_tab_login). Каждая кнопка рендерится
        только если соответствующий callback передан конструктору; если ни
        один не передан — строка кнопок не добавляется (пустая пара).
        Равная ширина — общий minimumWidth = максимум sizeHint обеих кнопок
        (тот же приём, что MainWindow._equalize_create_button_widths), плюс
        стрейч 1:1 — растягиваются поровну при наличии места."""
        if self._gen_callback is None and self._gen_settings_callback is None:
            return None, None

        gen_btn: QPushButton | None = None
        cfg_btn: QPushButton | None = None
        if self._gen_callback is not None:
            gen_btn = QPushButton("СГЕНЕРИРОВАТЬ ПАРОЛЬ")
            gen_btn.setToolTip("Сгенерировать значение")
            gen_btn.clicked.connect(
                lambda checked=False, e=edit: self._generate_into(e))
        if self._gen_settings_callback is not None:
            cfg_btn = QPushButton("ПАРАМЕТРЫ ГЕНЕРАЦИИ")
            cfg_btn.setToolTip("Настройки генерации пароля")
            cfg_btn.clicked.connect(
                lambda checked=False, e=edit: self._generate_via_settings(e))

        buttons = [b for b in (gen_btn, cfg_btn) if b is not None]
        width = max(b.sizeHint().width() for b in buttons)
        row = QHBoxLayout()
        row.setSpacing(5)
        for b in buttons:
            b.setMinimumWidth(width)
            row.addWidget(b, 1)
        outer.addLayout(row)
        return gen_btn, cfg_btn

    def _generate_into(self, edit: QLineEdit) -> None:
        if self._gen_callback is None:
            return
        edit.setText(self._gen_callback())

    def _generate_via_settings(self, edit: QLineEdit) -> None:
        """«ПАРАМЕТРЫ ГЕНЕРАЦИИ» этой строки: callback открывает диалог и
        возвращает пароль предпросмотра при сохранении (иначе None при
        отмене) — идентично AccountCardMixin.open_password_generator_settings,
        но подстановка идёт в поле пароля ИМЕННО этого экземпляра списка."""
        if self._gen_settings_callback is None:
            return
        result = self._gen_settings_callback()
        if result is not None:
            edit.setText(result)

    @staticmethod
    def _value(widget: QWidget) -> str:
        if isinstance(widget, QComboBox):
            return widget.currentText().strip()
        if isinstance(widget, CopyableTextEdit):    # включая MaskedTextEdit
            return widget.get_text()
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
            elif isinstance(w, CopyableTextEdit):
                # multiline: свой read-only/copy-btn/reveal — как на вкладке
                # «Фраза восстановления», не дублируем логику.
                w.set_editable(editable)
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
        if row.copy_btn is not None:
            row.copy_btn.setVisible(not editable)
        row.del_btn.setVisible(editable)
        # Индивидуальные кнопки поля: [КОП] — как общая (просмотр),
        # «СГЕНЕРИРОВАТЬ ПАРОЛЬ»/«ПАРАМЕТРЫ ГЕНЕРАЦИИ» — только в правке
        # (генерация имеет смысл лишь при вводе значения, как у аккаунта).
        for btn in row.field_copy_btns.values():
            btn.setVisible(not editable)
        for btn in row.field_gen_btns.values():
            btn.setVisible(editable)
        for btn in row.field_gen_settings_btns.values():
            btn.setVisible(editable)

    def _update_empty(self) -> None:
        self.empty_label.setVisible(not self.rows)
