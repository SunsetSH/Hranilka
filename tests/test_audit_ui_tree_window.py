"""Аудит 2026-11: дерево, буфер обмена, хоткеи, ресайз, busy-диалог (UI-слой)."""
import sys
import time

import pytest
from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtWidgets import QApplication

from hranilka.ui import chrome


@pytest.fixture
def window(qapp, tmp_path, monkeypatch, dispose_window):
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    win = main.MainWindow()
    yield win
    dispose_window(win)


def _populate(window):
    db = window.db
    fid = db.add_folder("Папка")
    sid = db.add_service("Сервис", fid)
    aid = db.add_account(sid, "Alpha")
    db.add_account(sid, "Beta")
    window._reload_tree()
    return fid, sid, aid


def test_reload_unblocks_signals_on_error(window, monkeypatch):
    _populate(window)

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(window, "populate_tree", boom)
    with pytest.raises(RuntimeError):
        window._reload_tree()
    assert not window.tree.signalsBlocked()


def test_reload_refreshes_current_tree_item(window):
    _, _, aid = _populate(window)
    item = window._find_leaf_item("account", aid)
    window.tree.setCurrentItem(item)
    window.current_tree_item = item
    window._reload_tree()
    assert window.current_tree_item is window.tree.currentItem()
    assert window._node(window.current_tree_item)["id"] == aid


def test_search_restores_expanded_state(window):
    _populate(window)
    top = window.tree.topLevelItem(0)
    top.setExpanded(False)
    window.on_search_changed("alpha")
    assert top.isExpanded()
    window.on_search_changed("")
    assert not top.isExpanded()


def test_search_debounce_and_sync(window, qapp):
    _populate(window)
    window.on_search_text_edited("alpha")
    assert window.search_text == ""
    deadline = time.monotonic() + 2
    while window.search_text != "alpha" and time.monotonic() < deadline:
        qapp.processEvents()
    assert window.search_text == "alpha"
    window.on_search_changed("")
    assert window.search_text == ""


def test_name_lower_precomputed(window):
    _populate(window)
    names = [window._node(i).get("name_lower") for i in window._iter_items()]
    assert "alpha" in names


def test_clipboard_helper_mime_and_clear_only_if_unchanged(window):
    window.config.set("clipboard_clear_secs", 5)
    chrome.copy_secret_to_clipboard("секрет")
    cb = QApplication.clipboard()
    assert cb.text() == "секрет"
    assert window._clip_timer.isActive()
    if sys.platform == "win32":
        pre = 'application/x-qt-windows-mime;value="'
        md = cb.mimeData()
        assert bytes(md.data(pre + 'CanIncludeInClipboardHistory"')) == b"\x00" * 4
        assert bytes(md.data(
            pre + 'ExcludeClipboardContentFromMonitorProcessing"')) == b"\x01\x00\x00\x00"
    cb.setText("чужое")
    window._clip_timer.timeout.emit()
    assert cb.text() == "чужое"


def test_hit_test():
    assert chrome.hit_test(0, 0, 100, 100) == "tl"
    assert chrome.hit_test(99, 99, 100, 100) == "br"
    assert chrome.hit_test(50, 0, 100, 100) == "t"
    assert chrome.hit_test(0, 50, 100, 100) == "l"
    assert chrome.hit_test(50, 50, 100, 100) is None


def test_titlebar_uses_own_max_flag():
    from hranilka.ui.titlebar import window_is_max

    class W:
        _is_max = True

        def isMaximized(self):
            return False

    assert window_is_max(W()) is True
    W._is_max = False
    assert window_is_max(W()) is False


def test_activity_throttle(window):
    window._act_mono = time.monotonic()
    stale = window._last_activity.addSecs(-100)
    window._last_activity = stale
    window.eventFilter(window, QEvent(QEvent.Type.MouseMove))
    assert window._last_activity == stale


def test_session_lock_gated_by_config(window, monkeypatch):
    called = []
    monkeypatch.setattr(window, "_lock_screen", lambda: called.append(1))
    window._current_account_id = 1
    window.config.set("lock_on_session_lock", False)
    window._on_session_event()
    QApplication.processEvents()
    assert not called
    window.config.set("lock_on_session_lock", True)
    window._on_session_event()
    QApplication.processEvents()
    assert called


def test_new_shortcuts_registered_tree_only(window):
    from hranilka.core import shortcuts
    assert shortcuts.DEFAULTS["rename_item"] == "F2"
    assert shortcuts.DEFAULTS["edit_selected"] == "Return"
    for aid in ("rename_item", "edit_selected"):
        sc = window._shortcuts[aid]
        assert sc.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut
        assert sc.parent() is window.tree


def test_busy_dialog_elapsed_and_give_up_button(window):
    from hranilka.ui import vault_controller as vc
    dlg = vc._BusyDialog(window.config, window, "msg", soft_limit=0)
    dlg._tick()
    assert not dlg._give_up_btn.isHidden()
    assert "Прошло" in dlg._elapsed.text()
    dlg._tick_timer.stop()


def test_busy_gate_released_after_give_up(window, qapp, monkeypatch):
    from hranilka.ui import vault_controller as vc

    def slow():
        time.sleep(2.0)
        return "ok"

    orig_init = vc._BusyDialog.__init__

    def init(self, *a, **k):
        orig_init(self, *a, **k)
        QTimer.singleShot(50, self._give_up)

    monkeypatch.setattr(vc._BusyDialog, "__init__", init)
    ok, _msg = window.vault.run_exclusive_busy(slow, "t")
    assert not ok and window.vault._vault_locked
    deadline = time.monotonic() + 8
    while window.vault._vault_locked and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert not window.vault._vault_locked
