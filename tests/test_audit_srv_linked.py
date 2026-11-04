"""LinkedServersWidget / server_item_display — только публичный API."""
from datetime import date, timedelta

import pytest

from hranilka.ui.widgets.srv_linked import (
    SERVER_TREE_PREFIX, LinkedServersWidget, server_item_display)


def _iso(days):
    return (date.today() + timedelta(days=days)).isoformat()


def test_display_prefix_and_name():
    text = server_item_display({"name": "vps1", "paid_until": _iso(60)})
    assert text == SERVER_TREE_PREFIX + "vps1"


def test_display_overdue_marker():
    assert server_item_display({"name": "x", "paid_until": _iso(-1)}).endswith("[!]")
    assert server_item_display({"name": "x", "paid_until": _iso(0)}).endswith("[!]")


def test_display_missing_values():
    assert server_item_display({}) == SERVER_TREE_PREFIX
    assert "[!]" not in server_item_display({"name": "x", "paid_until": "когда-нибудь"})


@pytest.fixture
def widget(qapp):
    w = LinkedServersWidget()
    yield w
    w.deleteLater()


def test_set_get_data_roundtrip(widget):
    widget.set_data([{"id": 3, "name": "a"}, {"id": 7, "name": "b"}])
    assert widget.get_data() == [3, 7]
    widget.set_data([])
    assert widget.get_data() == []


def test_set_data_replaces_previous(widget):
    widget.set_data([{"id": 1, "name": "a"}])
    widget.set_data([{"id": 2, "name": "b"}])
    assert widget.get_data() == [2]


def test_editable_toggles_add_and_delete_buttons(widget):
    widget.set_data([{"id": 1, "name": "a"}])
    widget.set_editable(False)
    assert widget.add_btn.isHidden()
    assert all(btn.isHidden() for _s, _r, btn in widget.items)
    widget.set_editable(True)
    assert not widget.add_btn.isHidden()
    assert all(not btn.isHidden() for _s, _r, btn in widget.items)


def test_delete_button_removes_link(widget):
    widget.set_data([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
    widget.set_editable(True)
    widget.items[0][2].click()
    assert widget.get_data() == [2]


def test_signals(widget):
    nav, add = [], []
    widget.navigate_requested.connect(nav.append)
    widget.add_requested.connect(lambda: add.append(1))
    widget.set_data([{"id": 5, "name": "a"}])
    row = widget.items[0][1]
    row.findChildren(type(widget.add_btn))[0].click()
    widget.add_btn.click()
    assert nav == [5] and add == [1]
