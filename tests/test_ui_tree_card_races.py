"""Регрессии: гонки карточки/дерева (удаление, перенос, ошибка загрузки,
привязки, скрытие серверов/фин, файлы стрелок)."""

import asyncio
import os
from types import SimpleNamespace

import pytest

from hranilka.core.nodetypes import ACCOUNT, FOLDER
from hranilka.ui.account_card import AccountCardMixin
from hranilka.ui.appearance import AppearanceMixin
from hranilka.ui.sidebar import SidebarMixin
from hranilka.ui.tree import TreeMixin


class _Msg:
    def showMessage(self, *a):
        pass


def _tree_self(account_id, selected=None):
    calls = []
    s = SimpleNamespace(
        db=SimpleNamespace(
            current_session=lambda: 1,
            delete_items=lambda *a, **k: [(ACCOUNT, 5)],
        ),
        _current_account_id=account_id, _current_fin=None, _current_server=None,
        current_server_data=None, current_tree_item=selected, is_editing=True,
        _any_db_changes=False, calls=calls,
    )

    async def run_async(fn, *a, **k):
        return [(ACCOUNT, 5)]

    s.db.run_async = run_async
    s._forget_leaf_cache = lambda *a: None
    s._reload_tree = lambda: calls.append("reload")
    s._show_placeholder = lambda: calls.append("placeholder")
    s._update_bin_button = lambda: None
    s.statusBar = lambda: _Msg()
    s._item_key = lambda item: item
    s._open_card_deleted = lambda d: TreeMixin._open_card_deleted(s, d)
    return s


def test_delete_other_node_keeps_open_card_and_edits():
    s = _tree_self(account_id=7)
    asyncio.run(TreeMixin._delete_items_async(
        s, [{"type": ACCOUNT, "id": 5}], False, False))
    assert s._current_account_id == 7 and s.is_editing is True
    assert s.calls == ["reload"]


def test_delete_open_card_resets():
    s = _tree_self(account_id=5)
    asyncio.run(TreeMixin._delete_items_async(
        s, [{"type": ACCOUNT, "id": 5}], False, False))
    assert s._current_account_id is None and s.is_editing is False
    assert "placeholder" in s.calls


def test_delete_folder_with_open_card_inside_resets():
    s = _tree_self(account_id=5)   # affected содержит (ACCOUNT, 5) — потомок папки
    asyncio.run(TreeMixin._delete_items_async(
        s, [{"type": FOLDER, "id": 1}], False, False))
    assert s._current_account_id is None


def test_tree_move_blocked_while_in_flight():
    fired = []
    s = SimpleNamespace(_move_in_flight=True, statusBar=lambda: _Msg())
    s._node = lambda i: i
    TreeMixin.on_tree_move(s, SimpleNamespace(sources=[], parent=None, anchor=None))
    assert not fired and s._move_in_flight is True


def test_move_flag_cleared_after_move():
    s = SimpleNamespace(_move_in_flight=True)

    async def failing(*a, **k):
        raise RuntimeError("x")

    s._run_then_reload = failing
    s.db = SimpleNamespace(move_nodes=None)
    with pytest.raises(RuntimeError):
        asyncio.run(TreeMixin._move_nodes_async(s, [], None, None, [], None))
    assert s._move_in_flight is False


def test_load_error_resets_current_account():
    class DB:
        def current_session(self):
            return 1

        async def run_async(self, *a, **k):
            raise RuntimeError("boom")

    s = SimpleNamespace(
        db=DB(), _edit_cache={}, _card_gen=3, _current_account_id=9,
        is_editing=True, errors=[], busy=[])
    s._show_placeholder = lambda: s.errors.append("ph")
    s._reset_failed_selection = lambda: s.errors.append("reset")
    s._show_card_error = lambda *a: s.errors.append("err")
    s._set_card_busy = lambda v: s.busy.append(v)
    asyncio.run(AccountCardMixin._load_account_into_ui(s, 9, 3))
    assert s._current_account_id is None and s.is_editing is False
    assert s.errors == ["ph", "reset", "err"] and s.busy == [False]


def test_link_ctx_valid():
    s = SimpleNamespace(_card_gen=2, _current_account_id=4,
                        db=SimpleNamespace(current_session=lambda: 1))
    def f(g, ses, k):
        return AccountCardMixin._link_ctx_valid(s, g, ses, k)

    assert f(2, 1, 4)
    assert not f(1, 1, 4) and not f(2, 0, 4) and not f(2, 1, 5)


def _sidebar_self(server=None, fin=None):
    gallery = SimpleNamespace(cancel_all_tasks=lambda: None)
    tabs = SimpleNamespace(f_gallery_widget=gallery)
    return SimpleNamespace(
        _card_gen=1, _card_busy=True, _current_server=server, _current_fin=fin,
        server_tabs=tabs, fin_tabs_by_type={"card": tabs}, _edit_cache={},
        _dirty_ids=set(), current_server_data=None, current_fin_data=None,
        is_editing=True, _show_placeholder=lambda: None)


def test_hide_servers_keeps_account_save_state():
    s = _sidebar_self()
    SidebarMixin._hide_servers_everywhere(s)
    assert s._card_gen == 1 and s._card_busy is True and s.is_editing is True
    s = _sidebar_self(server=("server", 1))
    SidebarMixin._hide_servers_everywhere(s)
    assert s._card_gen == 2 and s._card_busy is False


def test_hide_fin_keeps_account_save_state():
    s = _sidebar_self()
    SidebarMixin._hide_fin_everywhere(s)
    assert s._card_gen == 1 and s._card_busy is True
    s = _sidebar_self(fin=("card", 1))
    SidebarMixin._hide_fin_everywhere(s)
    assert s._card_gen == 2 and s._card_busy is False


def test_arrow_files_named_by_color_and_pruned(qapp):
    s = AppearanceMixin()
    s._branch_icon_dir_path = None
    s._branch_icon_gens = None
    try:
        first = s._render_branch_arrow_files("#FF0000", "#0000AA")
        second = s._render_branch_arrow_files("#00FF00", "#0000AA")
        assert first != second
        assert "FF0000" in first and "00FF00" in second
        for color in ("#111111", "#222222"):
            s._render_branch_arrow_files(color, "#0000AA")
        files = os.listdir(s._branch_icon_dir_path)
        assert not any("FF0000" in f for f in files)
        assert len(files) <= 8
    finally:
        s._cleanup_branch_icon_dir()


def test_appearance_sig_not_set_when_apply_fails():
    s = AppearanceMixin()
    s._appearance_sig = None
    s.config = SimpleNamespace(get=lambda k, d=None: d)
    s._applied_qss = None
    s._tree_icons_sig = None
    import hranilka.ui.appearance as ap

    orig = ap.retro_style.configure_app
    ap.retro_style.configure_app = lambda c: (_ for _ in ()).throw(RuntimeError("x"))
    try:
        with pytest.raises(RuntimeError):
            s.apply_appearance()
    finally:
        ap.retro_style.configure_app = orig
    assert s._appearance_sig is None
