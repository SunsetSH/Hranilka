"""Рефактор tree/tabs/models: обобщённые операции, PayloadTabs, common-хелпер."""
from hranilka.data.models import AccountData, ServerData
from hranilka.data.models.common import (PAYLOAD_VERSION, gallery_from_storage,
                                         gallery_to_storage)
from hranilka.data.models.fin_item import FinItemData
from hranilka.ui import tree as tree_mod
from hranilka.ui.tree import AccountTree, TreeMixin
from hranilka.ui.tree_dnd import AccountTree as DndTree


def test_reexports():
    assert AccountTree is DndTree
    assert hasattr(TreeMixin, "show_tree_context_menu")


def test_gallery_helpers_roundtrip():
    g = [{"data": b"x", "desc": "d", "image_id": 3, "blob_size": 1, "extra": 1}]
    out = gallery_to_storage(g)
    assert out == [{"data": b"x", "desc": "d", "image_id": 3, "blob_size": 1}]
    assert gallery_from_storage(None) == []
    for cls in (ServerData, FinItemData):
        d = cls()
        d.gallery = g
        st = d.to_storage()
        assert st["gallery"] == out
        assert st["payload"]["v"] == PAYLOAD_VERSION
        assert cls.from_storage(st).gallery == out
    a = AccountData()
    a.gallery = g
    assert a.to_storage()["gallery"] == out


class _Host(TreeMixin):
    def __init__(self):
        self.calls = []

        class DB:
            def set_server_favorites(s, ids, v): pass
            def move_accounts(s, ids, t): pass
        self.db = DB()
        self.nodes = {1: {"type": "account", "id": 10}, 2: {"type": "server", "id": 20}}

    def _node(self, item):
        return self.nodes[item]

    def _run_then_reload(self, method, args, err, status=None):
        self.calls.append((method.__name__, args, status))
        return None


def test_generic_ops_filter_by_kind(monkeypatch):
    monkeypatch.setattr(tree_mod.util, "fire", lambda r: r)
    h = _Host()
    h._move_accounts([1, 2], 5)
    h._set_server_favorite([1, 2], True)
    assert h.calls == [("move_accounts", ([10], 5), "Перемещено"),
                       ("set_server_favorites", ([20], True), None)]
