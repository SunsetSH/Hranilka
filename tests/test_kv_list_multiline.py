"""kind="multiline" в KeyValueListWidget: строки списка (адреса/ключи и т.п.)
с большими значениями (PEM-ключи) — CopyableTextEdit/MaskedTextEdit под
однострочными полями. По образцу test_kv_list_* в tests/test_fin_phase2.py.
"""
from hranilka.core.fin_types import FieldSpec
from hranilka.ui.widgets import KeyValueListWidget
from hranilka.ui.widgets.fields import CopyableTextEdit, MaskedTextEdit

_PEM = "-----BEGIN PRIVATE KEY-----\nMIIExampleKeyData\n-----END PRIVATE KEY-----"

# label — обычное однострочное поле, note — multiline (не секрет),
# key_pem — multiline + secret (PEM-ключ).
_ITEM_FIELDS = (
    FieldSpec("label", "Метка", "text", "Ключи"),
    FieldSpec("note", "Заметка", "multiline", "Ключи"),
    FieldSpec("key_pem", "PEM-ключ", "multiline", "Ключи", secret=True),
)


def test_kv_list_multiline_roundtrip(qapp):
    w = KeyValueListWidget(_ITEM_FIELDS)
    items = [{"label": "сервер", "note": "рабочий ключ", "key_pem": _PEM}]
    w.set_items(items)
    assert w.get_items() == items


def test_kv_list_multiline_widgets_are_textedit(qapp):
    w = KeyValueListWidget(_ITEM_FIELDS)
    w.add_row({"label": "a", "note": "текст", "key_pem": _PEM})
    row = w.rows[0]
    assert isinstance(row.fields["note"], CopyableTextEdit)
    assert not isinstance(row.fields["note"], MaskedTextEdit)
    assert isinstance(row.fields["key_pem"], MaskedTextEdit)


def test_kv_list_multiline_secret_masked_in_view(qapp):
    w = KeyValueListWidget(_ITEM_FIELDS)
    w.set_items([{"label": "a", "note": "n", "key_pem": _PEM}])
    w.set_editable(False)
    key_widget = w.rows[0].fields["key_pem"]
    shown = key_widget.text_edit.toPlainText()
    # Маска подменяет отображаемый текст, реальное значение доступно через
    # get_text() (как у MaskedTextEdit на вкладке «Фраза восстановления»).
    assert _PEM not in shown
    assert key_widget.get_text() == _PEM
    assert w.get_items()[0]["key_pem"] == _PEM


def test_kv_list_multiline_edit_view_toggle(qapp):
    w = KeyValueListWidget(_ITEM_FIELDS)
    w.set_items([{"label": "a", "note": "n", "key_pem": _PEM}])
    key_widget = w.rows[0].fields["key_pem"]
    note_widget = w.rows[0].fields["note"]

    w.set_editable(True)
    assert key_widget.text_edit.toPlainText() == _PEM     # правка — по настоящему тексту
    assert note_widget.text_edit.isReadOnly() is False

    w.set_editable(False)
    assert _PEM not in key_widget.text_edit.toPlainText()  # снова скрыто
    assert note_widget.text_edit.isReadOnly() is True
    assert key_widget.get_text() == _PEM                    # значение не потеряно


def test_kv_list_multiline_empty_rows_pruned(qapp, monkeypatch):
    w = KeyValueListWidget(_ITEM_FIELDS)
    w.set_editable(True)
    w.add_row({"label": "keep", "note": "", "key_pem": ""})
    w.add_row()                                       # полностью пустая строка
    assert len(w.rows) == 2
    assert w.get_items() == [{"label": "keep", "note": "", "key_pem": ""}]


def test_kv_list_multiline_row_copy_uses_secret_field(qapp):
    from PySide6.QtWidgets import QApplication
    w = KeyValueListWidget(_ITEM_FIELDS)
    w.set_items([{"label": "a", "note": "n", "key_pem": _PEM}])
    w.set_editable(False)
    row = w.rows[0]
    QApplication.clipboard().clear()
    copied = []
    w.copy_signal.connect(lambda: copied.append(True))
    w._copy_row(row)                                  # secret-поле выбирается для [КОП]
    assert QApplication.clipboard().text() == _PEM
    assert copied == [True]


# ─── row_group/narrow: многорядная раскладка строки (УИ §2026-07-15) ─────────
#
# Аддитивное расширение (панели сервера, ui/server_tabs.py): без row_group
# все однострочные поля по-прежнему собираются в один общий ряд (проверено
# выше остальными тестами файла); с row_group — несколько рядов, [КОП]/[X] —
# в конце первого. narrow сжимает поле по ширине текста-подсказки.

_ROW_GROUP_FIELDS = (
    FieldSpec("kind", "Тип", "enum", "Т", row_group="r1", narrow=True,
              options=("a", "b")),
    FieldSpec("url", "URL", "text", "Т", row_group="r1"),
    FieldSpec("login", "Логин", "text", "Т", row_group="r2"),
    FieldSpec("password", "Пароль", "text", "Т", secret=True, row_group="r2"),
)


def test_kv_list_row_group_splits_into_two_rows_with_buttons_on_first(qapp):
    w = KeyValueListWidget(_ROW_GROUP_FIELDS)
    w.add_row({"kind": "a", "url": "https://x", "login": "u", "password": "p"})
    row = w.rows[0]
    outer = row.widget.layout()
    assert outer.count() == 2                          # два ряда, без multiline

    row1 = outer.itemAt(0).layout()
    row2 = outer.itemAt(1).layout()
    # Ряд 1: enum(narrow) + url + [КОП] + [X].
    assert row1.count() == 4
    assert row1.itemAt(2).widget() is row.copy_btn
    assert row1.itemAt(3).widget() is row.del_btn
    # Ряд 2: login + password + reveal-кнопка секретного поля (без [КОП]/[X]).
    assert row2.count() == 3

    assert row.fields["kind"].currentText() == "a"
    assert row.fields["url"].text() == "https://x"


def test_kv_list_row_group_roundtrip(qapp):
    w = KeyValueListWidget(_ROW_GROUP_FIELDS)
    items = [{"kind": "b", "url": "https://y", "login": "root", "password": "pw"}]
    w.set_items(items)
    assert w.get_items() == items


def test_kv_list_narrow_field_fixed_width_by_placeholder(qapp):
    w = KeyValueListWidget(_ROW_GROUP_FIELDS)
    w.add_row({"kind": "a"})
    combo = w.rows[0].fields["kind"]
    expected = combo.fontMetrics().horizontalAdvance("Тип") + 40
    assert combo.minimumWidth() == expected
    assert combo.maximumWidth() == expected
    # url — без narrow, не сжато по placeholder (растягивается стрейчем ряда).
    assert w.rows[0].fields["url"].maximumWidth() > expected


def test_kv_list_no_row_group_still_one_row(qapp):
    """Поля без row_group (None у всех) — по-прежнему один общий ряд, как до
    добавления группировки (панели/пользователи/SSH-ключи не затронуты)."""
    w = KeyValueListWidget(_ITEM_FIELDS)                # без row_group
    w.add_row({"label": "a", "note": "n", "key_pem": _PEM})
    row = w.rows[0]
    outer = row.widget.layout()
    # label (text, единственное non-multiline поле) + [КОП] + [X] = 1 ряд;
    # note/key_pem (multiline) — отдельные строки ниже.
    line_row = outer.itemAt(0).layout()
    assert line_row.count() == 3
    assert line_row.itemAt(1).widget() is row.copy_btn
    assert line_row.itemAt(2).widget() is row.del_btn


# ─── copy_btn/gen_btn: индивидуальные кнопки поля (УИ §2026-07-15) ──────────

_COPY_GEN_FIELDS = (
    FieldSpec("kind", "Тип", "enum", "Т", row_group="r1", narrow=True,
              options=("a", "b")),
    FieldSpec("url", "URL", "text", "Т", row_group="r1"),
    FieldSpec("login", "Логин", "text", "Т", row_group="r2", copy_btn=True),
    FieldSpec("password", "Пароль", "text", "Т", secret=True, row_group="r2",
              copy_btn=True, gen_btn=True),
)


def test_kv_list_field_copy_btn_rendered_and_copies_field_value(qapp):
    from PySide6.QtWidgets import QApplication
    w = KeyValueListWidget(_COPY_GEN_FIELDS)
    w.set_items([{"kind": "a", "url": "https://x", "login": "u", "password": "p"}])
    w.set_editable(False)
    row = w.rows[0]
    assert "login" in row.field_copy_btns
    assert "password" in row.field_copy_btns
    assert not row.field_copy_btns["login"].isHidden()

    QApplication.clipboard().clear()
    copied = []
    w.copy_signal.connect(lambda: copied.append(True))
    row.field_copy_btns["login"].click()
    assert QApplication.clipboard().text() == "u"
    assert copied == [True]


def test_kv_list_field_copy_btn_standard_width(qapp):
    """Пофилдовая [КОП] (FieldSpec.copy_btn) — той же стандартной ширины, что
    строчная [КОП] (kv_list) и [КОП] карточек (ui/widgets/fields.py: 60px) —
    иначе текст обрезается («ОГ» вместо «КОП»)."""
    w = KeyValueListWidget(_COPY_GEN_FIELDS)
    w.set_items([{"kind": "a", "url": "https://x", "login": "u", "password": "p"}])
    row = w.rows[0]
    assert row.field_copy_btns["login"].width() == 60
    assert row.field_copy_btns["password"].width() == 60


def test_kv_list_field_copy_btn_hidden_while_editable(qapp):
    w = KeyValueListWidget(_COPY_GEN_FIELDS)
    w.set_items([{"kind": "a", "url": "https://x", "login": "u", "password": "p"}])
    row = w.rows[0]
    w.set_editable(True)
    assert row.field_copy_btns["login"].isHidden()
    w.set_editable(False)
    assert not row.field_copy_btns["login"].isHidden()


def test_kv_list_gen_btn_absent_without_any_callback(qapp):
    """gen_btn=True на поле не рендерит кнопки без gen_callback/
    gen_settings_callback — kv_list не падает и не создаёт кнопки «в никуда»."""
    w = KeyValueListWidget(_COPY_GEN_FIELDS)   # ни один callback не передан
    w.set_items([{"kind": "a", "url": "https://x", "login": "u", "password": "p"}])
    assert w.rows[0].field_gen_btns == {}
    assert w.rows[0].field_gen_settings_btns == {}


def test_kv_list_gen_btn_calls_callback_and_fills_field(qapp):
    calls = []

    def fake_gen():
        calls.append(1)
        return "Sup3r$ecret!"

    w = KeyValueListWidget(_COPY_GEN_FIELDS, gen_callback=fake_gen)
    w.set_items([{"kind": "a", "url": "https://x", "login": "u", "password": ""}])
    w.set_editable(True)
    row = w.rows[0]
    assert not row.field_gen_btns["password"].isHidden()

    row.field_gen_btns["password"].click()
    assert calls == [1]
    assert row.fields["password"].text() == "Sup3r$ecret!"


def test_kv_list_gen_btn_hidden_in_view_mode(qapp):
    w = KeyValueListWidget(_COPY_GEN_FIELDS, gen_callback=lambda: "x")
    w.set_items([{"kind": "a", "url": "https://x", "login": "u", "password": "p"}])
    w.set_editable(False)
    assert w.rows[0].field_gen_btns["password"].isHidden()


# ─── Раскладка «под полями строки» + «ПАРАМЕТРЫ ГЕНЕРАЦИИ» (УИ §2026-07-16,
# как в карточке аккаунта — ui/tabs.py: create_tab_login) ────────────────────

def test_kv_list_gen_buttons_rendered_below_field_rows_not_inline(qapp):
    """gen_btn больше НЕ добавляет кнопку инлайн в ряд поля (r2) — вместо
    этого под всеми полями строки появляется отдельный ряд с двумя кнопками
    равной ширины."""
    w = KeyValueListWidget(_COPY_GEN_FIELDS, gen_callback=lambda: "x",
                          gen_settings_callback=lambda: "y")
    w.add_row({"kind": "a", "url": "https://x", "login": "u", "password": "p"})
    row = w.rows[0]
    outer = row.widget.layout()
    assert outer.count() == 3                 # r1, r2, ряд кнопок генерации

    # r2: login+[КОП], password+reveal+[КОП] — БЕЗ инлайн-кнопки генерации.
    row2 = outer.itemAt(1).layout()
    assert row2.count() == 5
    assert row2.itemAt(0).widget() is row.fields["login"]
    assert row2.itemAt(2).widget() is row.fields["password"]
    assert row2.itemAt(4).widget() is row.field_copy_btns["password"]

    gen_row = outer.itemAt(2).layout()
    assert gen_row.count() == 2
    assert gen_row.itemAt(0).widget() is row.field_gen_btns["password"]
    assert gen_row.itemAt(1).widget() is row.field_gen_settings_btns["password"]


def test_kv_list_gen_buttons_equal_width(qapp):
    """«СГЕНЕРИРОВАТЬ ПАРОЛЬ»/«ПАРАМЕТРЫ ГЕНЕРАЦИИ» — равной ширины, несмотря
    на разную длину текста (общий minimumWidth = максимум sizeHint обеих,
    тот же приём, что MainWindow._equalize_create_button_widths)."""
    w = KeyValueListWidget(_COPY_GEN_FIELDS, gen_callback=lambda: "x",
                          gen_settings_callback=lambda: "y")
    w.add_row({"kind": "a", "url": "https://x", "login": "u", "password": "p"})
    row = w.rows[0]
    gen_btn = row.field_gen_btns["password"]
    cfg_btn = row.field_gen_settings_btns["password"]
    assert gen_btn.minimumWidth() == cfg_btn.minimumWidth() > 0
    # Стрейч 1:1 — растягиваются поровну при наличии свободного места.
    outer = row.widget.layout()
    gen_row = outer.itemAt(outer.count() - 1).layout()
    assert gen_row.stretch(0) == gen_row.stretch(1) == 1


def test_kv_list_gen_settings_btn_only_rendered_without_gen_callback(qapp):
    """gen_settings_callback без gen_callback — рендерится только «ПАРАМЕТРЫ
    ГЕНЕРАЦИИ» (одиночная кнопка, без пары)."""
    w = KeyValueListWidget(_COPY_GEN_FIELDS, gen_settings_callback=lambda: "y")
    w.add_row({"kind": "a", "url": "https://x", "login": "u", "password": "p"})
    row = w.rows[0]
    assert row.field_gen_btns == {}
    assert "password" in row.field_gen_settings_btns


def test_kv_list_gen_settings_btn_fills_field_from_callback_result(qapp):
    calls = []

    def fake_settings():
        calls.append(1)
        return "FromDialogPreview!1"

    w = KeyValueListWidget(_COPY_GEN_FIELDS, gen_settings_callback=fake_settings)
    w.set_items([{"kind": "a", "url": "https://x", "login": "u", "password": "old"}])
    w.set_editable(True)
    row = w.rows[0]

    row.field_gen_settings_btns["password"].click()

    assert calls == [1]
    assert row.fields["password"].text() == "FromDialogPreview!1"


def test_kv_list_gen_settings_btn_none_result_leaves_field_untouched(qapp):
    """Отмена диалога настроек (callback вернул None) — поле НЕ трогается."""
    w = KeyValueListWidget(_COPY_GEN_FIELDS, gen_settings_callback=lambda: None)
    w.set_items([{"kind": "a", "url": "https://x", "login": "u", "password": "old"}])
    w.set_editable(True)
    row = w.rows[0]

    row.field_gen_settings_btns["password"].click()

    assert row.fields["password"].text() == "old"


def test_kv_list_gen_settings_btn_hidden_in_view_mode(qapp):
    w = KeyValueListWidget(_COPY_GEN_FIELDS, gen_callback=lambda: "x",
                          gen_settings_callback=lambda: "y")
    w.set_items([{"kind": "a", "url": "https://x", "login": "u", "password": "p"}])
    w.set_editable(False)
    assert w.rows[0].field_gen_settings_btns["password"].isHidden()


# ─── Отступ многорядных строк списка (УИ §2026-07-15) ───────────────────────

def test_kv_list_multi_row_group_gets_extra_bottom_margin(qapp):
    """Строка с несколькими row_group-рядами (панели и т.п.) получает
    увеличенный нижний отступ — иначе соседние строки списка сливаются."""
    w = KeyValueListWidget(_COPY_GEN_FIELDS)               # 2 row_group-ряда
    w.add_row({"kind": "a", "url": "https://x", "login": "u", "password": "p"})
    margins = w.rows[0].widget.layout().contentsMargins()
    assert margins.bottom() > 0


def test_kv_list_single_row_no_extra_bottom_margin(qapp):
    """Однорядные списки (пользователи, фин-адреса/ключи без row_group) — без
    доп. отступа, межстрочный интервал не меняется."""
    single_row_fields = (
        FieldSpec("login", "Логин", "text", "Т"),
        FieldSpec("password", "Пароль", "text", "Т", secret=True),
    )
    w = KeyValueListWidget(single_row_fields)
    w.add_row({"login": "u", "password": "p"})
    margins = w.rows[0].widget.layout().contentsMargins()
    assert margins.bottom() == 0


def test_kv_list_multiline_row_gets_extra_bottom_margin(qapp):
    """multiline-поле (PEM-ключ и т.п.) тоже делает строку «многорядной» —
    визуально она не менее слитная, чем row_group."""
    w = KeyValueListWidget(_ITEM_FIELDS)                    # note/key_pem — multiline
    w.add_row({"label": "a", "note": "n", "key_pem": _PEM})
    margins = w.rows[0].widget.layout().contentsMargins()
    assert margins.bottom() > 0
