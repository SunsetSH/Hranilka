"""Аудит 2026-11, widgets: P9 (setUpdatesEnabled), единый LinkedItemsWidget,
RevealableRowsMixin (коды/вопросы), CopyableIsoDateField."""
import datetime as dt

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QLineEdit, QWidget

from hranilka.core.fin_types import FieldSpec
from hranilka.ui.widgets import (CodeListWidget, KeyValueListWidget,
                                 LinkedAccountsWidget, LinkedFinItemsWidget,
                                 LinkedServersWidget, SecretQuestionsWidget,
                                 CopyableDateField)
from hranilka.ui.widgets.common import updates_suspended
from hranilka.ui.widgets.fields import CopyableIsoDateField


def test_updates_suspended_restores_even_on_error(qapp):
    w = QWidget()
    with pytest.raises(RuntimeError):
        with updates_suspended(w):
            assert w.updatesEnabled() is False
            raise RuntimeError("x")
    assert w.updatesEnabled() is True


def _track_updates(widget):
    calls = []
    orig = widget.setUpdatesEnabled

    def spy(flag):
        calls.append(flag)
        orig(flag)
    widget.setUpdatesEnabled = spy
    return calls


def test_rebuilds_toggle_updates(qapp, pure_config):
    codes = CodeListWidget(config=pure_config)
    calls = _track_updates(codes)
    codes.set_data(["a", "b"])
    assert calls == [False, True] and codes.updatesEnabled()

    qs = SecretQuestionsWidget(config=pure_config)
    calls = _track_updates(qs)
    qs.set_data([{"q": "1", "a": "2"}])
    assert calls == [False, True]

    kv = KeyValueListWidget([FieldSpec("host", "Хост", "text", "Т")], config=pure_config)
    calls = _track_updates(kv)
    kv.set_items([{"host": "x"}])
    assert calls == [False, True]


# --- Linked*: паритет формы items и сигналов --------------------------------

def test_linked_accounts_parity(qapp):
    w = LinkedAccountsWidget()
    got = []
    w.navigate_requested.connect(got.append)
    w.set_data([{"id": 3, "name": "Gmail"}])
    assert w.get_data() == [3]
    aid, name, row, btn = w.items[0]
    assert (aid, name) == (3, "Gmail") and btn.isHidden()
    row.layout().itemAt(0).widget().click()
    assert got == [3]
    w.set_editable(True)
    assert w._editable is True
    w._remove_row(row)
    assert w.items == [] and w.get_data() == []
    # отложенное удаление строки - до того, как w соберёт GC (иначе падает позже)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_linked_fin_parity(qapp):
    w = LinkedFinItemsWidget()
    got = []
    w.navigate_requested.connect(lambda nt, i: got.append((nt, i)))
    w.set_data([{"id": 7, "item_type": "card", "name": "Bank", "card_last4": "1234"}])
    iid, node_type, row, _btn = w.items[0]
    assert iid == 7 and isinstance(node_type, str)
    assert "•1234" in row.layout().itemAt(0).widget().text()
    row.layout().itemAt(0).widget().click()
    assert got == [(node_type, 7)]


def test_linked_servers_parity(qapp):
    w = LinkedServersWidget()
    got = []
    w.navigate_requested.connect(got.append)
    w.set_data([{"id": 4, "name": "vps", "paid_until": None}])
    sid, row, _btn = w.items[0]
    assert sid == 4 and w.get_data() == [4]
    row.layout().itemAt(0).widget().click()
    assert got == [4]


def test_linked_old_modules_still_export(qapp):
    from hranilka.ui.widgets.fin_linked import LinkedFinItemsWidget as A, fin_item_display
    from hranilka.ui.widgets.srv_linked import LinkedServersWidget as B, server_item_display
    from hranilka.ui.widgets.linked import LinkedItemsWidget
    assert issubclass(A, LinkedItemsWidget) and issubclass(B, LinkedItemsWidget)
    assert fin_item_display({"name": "x"})
    assert server_item_display({"name": "s"}).startswith("[#]")


# --- Mixin: коды / вопросы ---------------------------------------------------

def test_mixin_reveal_echo_and_prune_codes(qapp, pure_config):
    w = CodeListWidget(config=pure_config)
    w.set_data(["abc", "  "])
    w.set_editable(True)
    assert all(e.echoMode() == QLineEdit.Normal for e, _b, _w in w.rows)
    w.set_editable(False)
    assert len(w.rows) == 1                       # пустой отсеян
    assert w.rows[0][0].echoMode() == QLineEdit.Password
    w.reveal_btn.setChecked(True)
    assert w.reveal_btn.text() == "СКРЫТЬ КОДЫ"
    assert w.rows[0][0].echoMode() == QLineEdit.Normal


def test_mixin_prune_and_reveal_questions(qapp, pure_config):
    w = SecretQuestionsWidget(config=pure_config)
    w.set_data([{"q": "q", "a": "a"}])
    w.add_row("", "")
    w.set_editable(True)
    w.set_editable(False)
    assert len(w.rows) == 1
    assert w.rows[0][1].echoMode() == QLineEdit.Password
    w.reveal_btn.setChecked(True)
    assert w.reveal_btn.text() == "СКРЫТЬ ОТВЕТЫ"
    w.set_editable(True)                          # вход в правку сбрасывает reveal
    assert w.reveal_btn.isChecked() is False


# --- CopyableIsoDateField ----------------------------------------------------

def test_iso_date_field_roundtrip(qapp):
    f = CopyableIsoDateField()
    f.set_text("2026-11-03")
    assert f.get_text() == "2026-11-03"
    assert f._inner.get_date().date() == dt.date(2026, 11, 3)
    f.set_text("")
    assert f.get_text() == ""


def test_iso_date_field_preserves_unrecognized_until_user_edit(qapp):
    f = CopyableIsoDateField()
    f.set_text("когда-то")
    assert f._inner.date_widget.text().strip(" .") == ""
    assert f.get_text() == "когда-то"
    f._inner.date_widget.textEdited.emit("1")
    assert f.get_text() == ""


def test_copyable_date_field_still_exported(qapp):
    assert CopyableDateField().get_date() is None
