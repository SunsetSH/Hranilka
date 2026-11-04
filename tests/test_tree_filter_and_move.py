"""Фильтр избранного, перенос move_nodes, удаление пустых контейнеров."""
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QTreeWidget, QTreeWidgetItem

from hranilka.core.nodetypes import ACCOUNT, CARD, FOLDER, SERVER, SERVICE
from hranilka.data.database import Database
from hranilka.ui.tree import TreeMixin
from hranilka.ui.tree_menu import TreeMenuMixin


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    d.connect()
    d.create_tables()
    yield d
    d.close()


def _order(db, table, where="1=1"):
    db.cursor.execute(f"SELECT id FROM {table} WHERE {where} ORDER BY sort_order")
    return [r["id"] for r in db.cursor.fetchall()]


def test_move_nodes_moves_all_types_with_order_atomically(db):
    folder = db.add_folder("Ф")
    target = db.add_service("Цель")
    old = db.add_account(target, "старый")
    acc = db.add_account(None, "новый")
    card = db.add_fin_item(None, "bank_card", "карта")
    srv = db.add_server(None, "сервер")
    db.move_nodes([(ACCOUNT, acc), (CARD, card), (SERVER, srv)], target, None,
                  [(ACCOUNT, acc), (ACCOUNT, old), (CARD, card), (SERVER, srv)])
    assert _order(db, "accounts", f"service_id = {target}") == [acc, old]
    assert _order(db, "fin_items", f"service_id = {target}") == [card]
    assert _order(db, "servers", f"service_id = {target}") == [srv]
    other = db.add_service("В папку")
    db.move_nodes([(SERVICE, other)], None, folder)
    assert _order(db, "services", f"folder_id = {folder}") == [other]


def test_move_nodes_rolls_back_on_invalid_item(db):
    a = db.add_service("A")
    b = db.add_service("B")
    acc = db.add_account(None, "x")
    with pytest.raises(ValueError):
        db.move_nodes([(ACCOUNT, acc), (SERVICE, b)], a, None)
    db.cursor.execute("SELECT service_id FROM accounts WHERE id = ?", (acc,))
    assert db.cursor.fetchone()["service_id"] is None


class _Cfg:
    def get(self, key, default=None):
        return default


class _Host(TreeMixin):
    def __init__(self):
        self.tree = QTreeWidget()
        self.search_text = ""
        self.config = _Cfg()


def _node(parent, kind, node_id, name, favorite=False):
    item = QTreeWidgetItem(parent, [name])
    item.setData(0, Qt.UserRole, {"type": kind, "id": node_id, "name": name,
                                  "is_favorite": favorite})
    return item


def test_favorites_filter_hides_rest_and_restores_expansion(qapp):
    host = _Host()
    folder = _node(host.tree, FOLDER, 1, "Папка")
    fav = _node(folder, ACCOUNT, 2, "Избранный", favorite=True)
    plain = _node(folder, ACCOUNT, 3, "Обычный")
    empty = _node(host.tree, SERVICE, 4, "Без избранного")
    _node(empty, ACCOUNT, 5, "Другой")
    host.on_favorites_only_toggled(True)
    assert not fav.isHidden() and plain.isHidden()
    assert not folder.isHidden() and folder.isExpanded() and empty.isHidden()
    host.on_favorites_only_toggled(False)
    assert not plain.isHidden() and not empty.isHidden()
    assert not folder.isExpanded()


def test_favorites_filter_combines_with_search(qapp):
    host = _Host()
    a = _node(host.tree, ACCOUNT, 1, "alpha", favorite=True)
    b = _node(host.tree, ACCOUNT, 2, "beta", favorite=True)
    host.search_text = "alp"
    host.on_favorites_only_toggled(True)
    assert not a.isHidden() and b.isHidden()


class _Menu(TreeMenuMixin):
    def __init__(self):
        self.calls = []

    def delete_items(self, selected, keep=False, mention_keep=True):
        self.calls.append((keep, mention_keep))


def test_empty_container_has_plain_delete(qapp):
    tree = QTreeWidget()
    empty = _node(tree, SERVICE, 1, "Пустой")
    host, menu = _Menu(), QMenu()
    host._add_container_delete(menu, [empty])
    actions = [a for a in menu.actions() if not a.isSeparator()]
    assert [a.text() for a in actions] == ["Удалить"] and actions[0].menu() is None
    actions[0].trigger()
    assert host.calls == [(True, False)]        # скрытые записи не теряются


def test_filled_container_keeps_delete_choice(qapp):
    tree = QTreeWidget()
    full = _node(tree, FOLDER, 1, "Полная")
    _node(full, ACCOUNT, 2, "x")
    host, menu = _Menu(), QMenu()
    host._add_container_delete(menu, [full])
    sub = [a for a in menu.actions() if a.menu()]
    assert len(sub) == 1 and len(sub[0].menu().actions()) == 2
    assert sqlite3 is not None
