"""Аудит 2026-11, widgets: у кнопок без текстовой подписи ([X], [КОП], [*])
есть accessibleName; порядок Tab в галерее."""
from PySide6.QtWidgets import QPushButton

from hranilka.core.fin_types import FieldSpec
from hranilka.ui.widgets import (CodeListWidget, CopyableDateField,
                                 CopyableField, CopyableTextEdit, ExpiryField,
                                 GalleryWidget, KeyValueListWidget,
                                 LinkedAccountsWidget, LinkedFinItemsWidget,
                                 LinkedServersWidget, MaskedCardNumberField,
                                 MaskedTextEdit, SecretQuestionsWidget)

_ICON_TEXTS = {"[X]", "[КОП]", "*", "A"}


def _icon_buttons(root):
    return [b for b in root.findChildren(QPushButton) if b.text() in _ICON_TEXTS]


def _assert_named(root):
    btns = _icon_buttons(root)
    assert btns, "ожидались иконочные кнопки"
    for b in btns:
        assert b.accessibleName().strip(), f"{b.text()} без accessibleName"


def test_simple_fields_icon_buttons_named(qapp):
    for w in (CopyableField(is_password=True), CopyableDateField(),
              CopyableTextEdit(), MaskedTextEdit(), ExpiryField(),
              MaskedCardNumberField()):
        _assert_named(w)


def test_list_widgets_icon_buttons_named(qapp, pure_config):
    codes = CodeListWidget(config=pure_config)
    codes.set_data(["1"])
    _assert_named(codes)
    qs = SecretQuestionsWidget(config=pure_config)
    qs.set_data([{"q": "a", "a": "b"}])
    _assert_named(qs)
    kv = KeyValueListWidget([FieldSpec("k", "К", "text", "Т", secret=True)], config=pure_config)
    kv.set_items([{"k": "v"}])
    _assert_named(kv)
    for w in (LinkedAccountsWidget(), LinkedFinItemsWidget(),
              LinkedServersWidget()):
        w.set_data([{"id": 1, "name": "a"}])
        _assert_named(w)


def test_gallery_item_accessibility_and_tab_order(qapp):
    gw = GalleryWidget()
    gw.add_item(None, image_id=1)
    gw.add_item(None, image_id=2)
    for it in gw.items:
        assert it["thumb"].accessibleName()
        assert it["del"].accessibleName()
        assert it["desc"].accessibleName()
    expected = [gw.upload_btn, gw.paste_btn]
    for it in gw.items:
        expected += [it["thumb"], it["desc"], it["del"]]
    # Реальный порядок фокуса: обход цепочки, только фокусируемые виджеты.
    actual, w = [], gw.upload_btn
    for _ in range(200):
        if w in expected and w not in actual:
            actual.append(w)
        w = w.nextInFocusChain()
        if w is gw.upload_btn:
            break
    assert actual == expected
