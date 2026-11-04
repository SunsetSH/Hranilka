"""Регрессии улучшенного drag-and-drop дерева."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTreeWidgetItem

from hranilka.core.nodetypes import ACCOUNT, CARD, FOLDER, SERVER, SERVICE, WALLET
from hranilka.ui.tree import AccountTree, MovePlan, TreeMixin


def _item(parent, item_id, name, item_type=ACCOUNT, favorite=False):
    item = QTreeWidgetItem(parent, [name])
    item.setData(0, Qt.ItemDataRole.UserRole, {
        "id": item_id,
        "name": name,
        "type": item_type,
        "is_favorite": favorite,
    })
    return item


def _visible_tree(qapp):
    tree = AccountTree()
    tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    tree.resize(420, 300)
    tree.show()
    qapp.processEvents()
    return tree


def _point_in_half(tree, item, upper):
    rect = tree.visualItemRect(item)
    assert rect.isValid() and rect.height() >= 2
    y = rect.top() + (rect.height() // 4 if upper else 3 * rect.height() // 4)
    return rect.center().x(), y


def test_drop_plan_splits_the_whole_row_into_two_large_zones(qapp):
    tree = _visible_tree(qapp)
    source = _item(tree, 1, "Первый")
    target = _item(tree, 2, "Второй")
    source.setSelected(True)
    qapp.processEvents()

    from PySide6.QtCore import QPoint
    upper_plan, upper_hint = tree._drop_plan(
        QPoint(*_point_in_half(tree, target, upper=True)))
    lower_plan, lower_hint = tree._drop_plan(
        QPoint(*_point_in_half(tree, target, upper=False)))

    assert upper_plan is not None and upper_plan[-1] is True
    assert lower_plan is not None and lower_plan[-1] is False
    assert upper_hint == "Вставить перед «Второй»"
    assert lower_hint == "Вставить после «Второй»"
    tree.close()


def test_reorder_moves_multiple_rows_as_one_stable_block(qapp):
    tree = _visible_tree(qapp)
    _item(tree, 1, "A")
    b = _item(tree, 2, "B")
    c = _item(tree, 3, "C")
    d = _item(tree, 4, "D")
    b.setSelected(True)
    c.setSelected(True)
    qapp.processEvents()

    plan = ([b, c], None, ACCOUNT, d, False)
    assert tree._apply_reorder(plan) is True
    assert [tree.topLevelItem(i).text(0) for i in range(4)] == ["A", "D", "B", "C"]
    assert b.isSelected() and c.isSelected()
    tree.close()


def test_drop_event_accepts_move_and_reports_affected_group(qapp):
    tree = _visible_tree(qapp)
    source = _item(tree, 1, "A")
    _item(tree, 2, "B")
    target = _item(tree, 3, "C")
    source.setSelected(True)
    qapp.processEvents()
    x, y = _point_in_half(tree, target, upper=False)

    class _DropEvent:
        accepted = False
        ignored = False
        action = None

        def position(self):
            return QPointF(x, y)

        def setDropAction(self, action):
            self.action = action

        def accept(self):
            self.accepted = True

        def ignore(self):
            self.ignored = True

    changed = []
    tree.order_changed.connect(lambda parent, group: changed.append((parent, group)))
    event = _DropEvent()
    tree.dropEvent(event)

    assert event.accepted is True and event.ignored is False
    assert event.action == Qt.DropAction.MoveAction
    assert [tree.topLevelItem(i).text(0) for i in range(3)] == ["B", "C", "A"]
    assert changed == [(None, ACCOUNT)]
    tree.close()


def test_cards_and_wallets_share_one_reorder_group(qapp):
    tree = _visible_tree(qapp)
    card = _item(tree, 1, "Карта", CARD)
    wallet = _item(tree, 2, "Кошелёк", WALLET)
    card.setSelected(True)
    qapp.processEvents()

    from PySide6.QtCore import QPoint
    plan, _hint = tree._drop_plan(
        QPoint(*_point_in_half(tree, wallet, upper=False)))

    assert plan is not None
    assert plan[2] == AccountTree.FIN_GROUP
    tree.close()


def test_drop_rejects_other_parent_and_favorite_bucket(qapp):
    tree = _visible_tree(qapp)
    service_a = _item(tree, 10, "Сервис A", SERVICE)
    service_b = _item(tree, 11, "Сервис B", SERVICE)
    source = _item(service_a, 1, "Обычный")
    other_parent = _item(service_b, 2, "Другой сервис")
    favorite = _item(service_a, 3, "Избранный", favorite=True)
    service_a.setExpanded(True)
    service_b.setExpanded(True)
    source.setSelected(True)
    qapp.processEvents()

    from PySide6.QtCore import QPoint
    parent_plan, parent_reason = tree._drop_plan(
        QPoint(*_point_in_half(tree, other_parent, upper=True)))
    favorite_plan, favorite_reason = tree._drop_plan(
        QPoint(*_point_in_half(tree, favorite, upper=True)))

    assert parent_plan is None
    assert "одной группы" in parent_reason
    assert favorite_plan is None
    assert "Избранные" in favorite_reason
    tree.close()


def test_search_disables_drag_and_drop_until_filter_is_cleared(qapp):
    host = SimpleNamespace(
        tree=AccountTree(),
        sort_mode="manual",
        search_text="",
    )

    TreeMixin._update_dnd_mode(host)
    assert host.tree.dragEnabled() is True
    assert host.tree.acceptDrops() is True
    assert host.tree.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove

    host.search_text = "аккаунт"
    TreeMixin._update_dnd_mode(host)
    assert host.tree.dragEnabled() is False
    assert host.tree.acceptDrops() is False
    assert host.tree.dragDropMode() == QAbstractItemView.DragDropMode.NoDragDrop


def test_container_groups_remain_separate(qapp):
    tree = _visible_tree(qapp)
    folder = _item(tree, 1, "Папка", FOLDER)
    service = _item(tree, 2, "Сервис", SERVICE)
    folder.setSelected(True)
    qapp.processEvents()

    from PySide6.QtCore import QPoint
    plan, reason = tree._drop_plan(
        QPoint(*_point_in_half(tree, service, upper=True)))

    assert plan is None
    assert "одной группы" in reason
    tree.close()


def test_order_save_targets_only_the_changed_database_group(qapp, monkeypatch):
    tree = AccountTree()
    service = _item(tree, 10, "Сервис", SERVICE)
    _item(service, 1, "Аккаунт", ACCOUNT)
    _item(service, 2, "Карта", CARD)
    _item(service, 3, "Кошелёк", WALLET)

    methods = {
        "folders": lambda ids: None,
        "services": lambda ids: None,
        "accounts": lambda ids: None,
        "fin": lambda ids: None,
        "servers": lambda ids: None,
    }
    host = SimpleNamespace(
        tree=tree,
        db=SimpleNamespace(
            set_folders_order=methods["folders"],
            set_services_order=methods["services"],
            set_accounts_order=methods["accounts"],
            set_fin_items_order=methods["fin"],
            set_servers_order=methods["servers"],
        ),
    )
    host._node = TreeMixin._node.__get__(host)
    captured = []
    host._save_order_async = lambda ops: captured.extend(ops)
    monkeypatch.setattr("hranilka.ui.tree.util.fire", lambda result: result)

    TreeMixin.on_tree_order_changed(host, service, AccountTree.FIN_GROUP)

    assert captured == [(methods["fin"], [2, 3])]


def _drag_tree(qapp, count=5):
    tree = _visible_tree(qapp)
    tree.setDragEnabled(True)
    for i in range(count):
        _item(tree, i, f"r{i}")
    qapp.processEvents()
    tree.started = []
    tree.startDrag = lambda _actions: tree.started.append(
        [i.text(0) for i in tree.selectedItems()])
    return tree


def _left_drag(tree, row_from, row_to, modifiers=Qt.KeyboardModifier.NoModifier):
    vp = tree.viewport()
    start = tree.visualItemRect(tree.topLevelItem(row_from)).center()
    end = tree.visualItemRect(tree.topLevelItem(row_to)).center()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, modifiers, start)
    for y in range(start.y(), end.y() + 1, 4):
        pos = QPointF(start.x(), y)
        QApplication.sendEvent(vp, QMouseEvent(
            QEvent.Type.MouseMove, pos, QPointF(vp.mapToGlobal(pos.toPoint())),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, modifiers))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, modifiers, end)


def test_plain_drag_starts_drag_of_pressed_row_only(qapp):
    tree = _drag_tree(qapp)
    _left_drag(tree, 1, 3)
    assert tree.started == [["r1"]]
    tree.close()


def test_tree_rebuild_during_press_still_drags_not_selects(qapp):
    """Регрессия: перестроение дерева обнуляло pressedIndex Qt, и протягивание
    становилось выделением диапазона, как с зажатым Shift."""
    tree = _drag_tree(qapp)

    def rebuild(*_):
        tree.currentItemChanged.disconnect(rebuild)
        was_blocked = tree.blockSignals(True)
        tree.clear()
        for i in range(5):
            _item(tree, i, f"r{i}")
        tree.blockSignals(was_blocked)

    tree.currentItemChanged.connect(rebuild)
    _left_drag(tree, 1, 3)
    assert tree.started == [["r1"]]
    tree.close()


def test_disabled_drag_does_not_turn_into_range_selection(qapp):
    tree = _drag_tree(qapp)
    tree.setDragEnabled(False)
    reasons = []
    tree.drop_rejected.connect(reasons.append)
    _left_drag(tree, 1, 3)
    assert tree.started == []
    assert [i.text(0) for i in tree.selectedItems()] == ["r1"]
    assert len(reasons) == 1
    tree.close()


def test_shift_drag_keeps_range_selection(qapp):
    tree = _drag_tree(qapp)
    tree.setDragEnabled(False)
    tree.setCurrentItem(tree.topLevelItem(1))
    _left_drag(tree, 1, 3, Qt.KeyboardModifier.ShiftModifier)
    assert len(tree.selectedItems()) >= 2
    tree.close()


def _zones_tree(qapp):
    tree = _visible_tree(qapp)
    folder = _item(tree, 1, "Папка", FOLDER)
    in_folder = _item(folder, 2, "Сервис в папке", SERVICE)
    service = _item(tree, 3, "Сервис", SERVICE)
    card = _item(service, 4, "Карта", CARD)
    server = _item(tree, 5, "Сервер", SERVER)
    wallet = _item(tree, 6, "Кошелёк", WALLET)
    tree.expandAll()
    qapp.processEvents()
    return tree, folder, in_folder, service, card, server, wallet


@pytest.mark.parametrize("source_name", ["card", "server", "wallet"])
def test_leaf_drops_into_service_and_folder(qapp, source_name):
    tree, folder, in_folder, service, card, server, wallet = _zones_tree(qapp)
    source = {"card": card, "server": server, "wallet": wallet}[source_name]
    source.setSelected(True)
    from PySide6.QtCore import QPoint
    for target in (folder, in_folder):
        plan, _ = tree._resolve_drop(QPoint(tree.visualItemRect(target).center()))
        assert isinstance(plan, MovePlan) and plan.parent is target
    tree.close()


def test_service_moves_into_folder_and_back_to_root(qapp):
    tree, folder, in_folder, service, _card, server, _wallet = _zones_tree(qapp)
    service.setSelected(True)
    from PySide6.QtCore import QPoint
    plan, _ = tree._resolve_drop(QPoint(tree.visualItemRect(folder).center()))
    assert isinstance(plan, MovePlan) and plan.parent is folder
    service.setSelected(False)
    in_folder.setSelected(True)
    plan, _ = tree._resolve_drop(
        QPoint(*_point_in_half(tree, server, upper=True)))
    assert isinstance(plan, MovePlan) and plan.parent is None
    assert plan.anchor is server and plan.before is True
    tree.close()


def test_folder_cannot_be_nested(qapp):
    tree, folder, _in_folder, service, *_rest = _zones_tree(qapp)
    folder.setSelected(True)
    from PySide6.QtCore import QPoint
    plan, reason = tree._resolve_drop(QPoint(tree.visualItemRect(service).center()))
    assert plan is None and "корне" in reason
    tree.close()


def test_dropping_a_service_on_itself_or_its_child_is_rejected(qapp):
    tree, _f, _s, service, card, *_rest = _zones_tree(qapp)
    service.setSelected(True)
    from PySide6.QtCore import QPoint
    plan, _ = tree._resolve_drop(QPoint(tree.visualItemRect(card).center()))
    assert plan is None
    tree.close()


def test_auto_sort_moves_without_position_and_refuses_reorder(qapp):
    tree, folder, _s, _service, _c, server, wallet = _zones_tree(qapp)
    tree.set_reorder_enabled(False)
    server.setSelected(True)
    from PySide6.QtCore import QPoint
    plan, _ = tree._resolve_drop(QPoint(tree.visualItemRect(folder).center()))
    assert plan.anchor is None
    plan, reason = tree._resolve_drop(
        QPoint(*_point_in_half(tree, wallet, upper=False)))
    assert plan is None and "ручной" in reason
    tree.close()


def test_order_after_move_inserts_next_to_anchor_inside_its_group(qapp):
    tree = _visible_tree(qapp)
    folder = _item(tree, 1, "Папка", FOLDER)
    _item(folder, 20, "Сервис", SERVICE)
    a = _item(folder, 2, "a")
    b = _item(folder, 3, "b")
    free = _item(tree, 4, "free")
    wallet = _item(tree, 5, "wallet", WALLET)
    host = SimpleNamespace(tree=tree)
    for name in ("_node", "_item_key", "_tree_order"):
        setattr(host, name, getattr(TreeMixin, name).__get__(host))
    plan = MovePlan((free, wallet), folder, b, True)
    ordered = TreeMixin._order_after_move(host, plan)
    assert ordered == [(SERVICE, 20), (ACCOUNT, 2), (ACCOUNT, 4), (ACCOUNT, 3),
                       (WALLET, 5)]
    assert a is not None
    tree.close()
