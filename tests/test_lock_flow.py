"""Регрессии авто-блокировки, run_exclusive_busy, wait_idle и конфликта записи
(chrome.py / vault_controller.py / db_lifecycle.py / theme.py)."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QDateTime, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication



@pytest.fixture
def window(qapp, tmp_path, monkeypatch, dispose_window):
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    win = main.MainWindow()
    yield win
    dispose_window(win)


def _encrypted_window(window, monkeypatch, lock_impl=None):
    """Окно, которое считает базу зашифрованной; реальные lock/пароль подменены."""
    window.db.encrypted = True
    monkeypatch.setattr(window.db, "lock", lock_impl or (lambda force=False: None))
    monkeypatch.setattr(window, "_open_database", lambda parent=None: True)
    monkeypatch.setattr(window, "_create_tables_or_exit", lambda: None)


def _make_leaf(window):
    aid = window.db.add_account(None, "Секретный акк")
    window._reload_tree()
    window.tabs.f_password.set_text("hunter2")
    window.tabs.f_recovery.set_text("seed words")
    window.search_box.setText("секрет")
    window.search_text = "секрет"
    window._current_account_id = aid
    return aid


# ─── 1. Таймер не останавливается, отказ блокировки не стирает карточку ───────

def test_idle_timer_not_stopped_by_lock_screen(window, monkeypatch):
    _encrypted_window(window, monkeypatch)
    window._idle_timer.start(30000)
    _make_leaf(window)
    window._lock_screen()
    assert window._idle_timer.isActive()


def test_failed_lock_keeps_card_and_timer(window, monkeypatch):
    def boom(force=False):
        raise OSError("нет места")
    _encrypted_window(window, monkeypatch, lock_impl=boom)
    shown = []
    monkeypatch.setattr("hranilka.ui.theme.themed_info",
                        lambda *a, **k: shown.append(a))
    window._idle_timer.start(30000)
    aid = _make_leaf(window)
    window._lock_screen()
    assert shown, "пользователь уведомлён об отказе"
    assert window._current_account_id == aid
    assert window.tabs.f_password.get_text() == "hunter2"
    assert window.tree.topLevelItemCount() == 1
    assert window.search_box.text() == "секрет"
    assert window._idle_timer.isActive()
    assert not window._locking and not window._unlocking


def test_conflict_declined_keeps_card(window, monkeypatch):
    from hranilka.data.database import VaultConflictError

    def conflict(force=False):
        raise VaultConflictError("x")
    _encrypted_window(window, monkeypatch, lock_impl=conflict)
    monkeypatch.setattr("hranilka.ui.theme.themed_confirm", lambda *a, **k: False)
    aid = _make_leaf(window)
    window._lock_screen()
    assert window._current_account_id == aid
    assert window.tabs.f_password.get_text() == "hunter2"


# ─── 3. Успешная блокировка очищает всё и разблокировка перезагружает ────────

def test_lock_clears_secrets_tree_and_search_then_reloads(window, monkeypatch):
    seen = {}
    window_ref = window

    def fake_open(parent=None):
        # В момент запроса пароля на экране нет ни секретов, ни дерева, ни поиска.
        seen["pwd"] = window_ref.tabs.f_password.get_text()
        seen["recovery"] = window_ref.tabs.f_recovery.get_text()
        seen["tree"] = window_ref.tree.topLevelItemCount()
        seen["search"] = window_ref.search_box.text()
        seen["unlocking"] = window_ref._unlocking
        return True
    _encrypted_window(window, monkeypatch)
    monkeypatch.setattr(window, "_open_database", fake_open)
    _make_leaf(window)
    window._lock_screen()
    assert seen == {"pwd": "", "recovery": "", "tree": 0, "search": "",
                    "unlocking": True}
    # Поиск возвращается после разблокировки (снимок), карточка закрыта.
    assert window.search_text == "секрет"
    assert window._current_account_id is None
    # После разблокировки дерево перестроено из БД.
    assert window.tree.topLevelItemCount() == 1
    assert window._idle_timer is not None


def test_session_lock_path_clears_card(window, monkeypatch):
    """_lock_screen_on_session идёт тем же путём (модальные окна и очистка)."""
    _encrypted_window(window, monkeypatch)
    _make_leaf(window)
    window._lock_screen_on_session()
    assert window.tabs.f_password.get_text() == ""


def test_plaintext_lock_clears_card_keeps_tree(window):
    window.db.add_account(None, "Акк")
    window._reload_tree()
    window.tabs.f_password.set_text("hunter2")
    window._current_account_id = 1
    window._lock_screen()
    assert window.tabs.f_password.get_text() == ""
    assert window._current_account_id is None
    assert window.tree.topLevelItemCount() == 1      # режим «скрыть карточку»


# ─── 2. Модальные окна ───────────────────────────────────────────────────────

def _show_modal(dlg):
    from PySide6.QtCore import Qt
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.show()
    QApplication.processEvents()


def test_force_close_default_no_prompt(qapp):
    from hranilka.ui.theme import ThemedDialog
    dlg = ThemedDialog(_cfg())
    _show_modal(dlg)
    dlg.force_close()
    assert not dlg.isVisible()
    assert dlg.result() == 0


def _cfg():
    class C(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)
    return C()


def test_lock_force_closes_modal_without_reject(window, monkeypatch):
    from hranilka.ui.theme import ThemedDialog
    calls = []

    class Dlg(ThemedDialog):
        def reject(self):
            calls.append("reject")           # вложенное подтверждение
            super().reject()

        def force_close(self):
            calls.append("force")
            super().force_close()
    dlg = Dlg(window.config, window)
    _show_modal(dlg)
    assert window._close_modals_for_lock() is True
    assert calls == ["force"]
    assert not dlg.isVisible()


def test_lock_deferred_for_recovery_dialog(window):
    dlg = _make_recovery(window)
    _show_modal(dlg)
    assert window._close_modals_for_lock() is False
    assert dlg.isVisible()                    # одноразовый код не потерян
    dlg.hide()


def _make_recovery(window):
    from hranilka.ui.dialogs.recovery import RecoveryCodeDialog
    return RecoveryCodeDialog(window.config, window)


def test_lock_deferred_for_busy_dialog(window):
    from hranilka.ui.vault_controller import _BusyDialog
    dlg = _BusyDialog(window.config, window, "x")
    _show_modal(dlg)
    assert window._close_modals_for_lock() is False
    dlg._tick_timer.stop()
    dlg.hide()


# ─── 8. Re-entrancy: _check_idle не работает во время closeEvent / lock ──────

@pytest.mark.parametrize("flag", ["_closing", "_locking"])
def test_check_idle_guarded(window, monkeypatch, flag):
    window.config.set("idle_lock_mins", 1)
    window._current_account_id = 5
    window._last_activity = QDateTime.currentDateTime().addSecs(-600)
    called = []
    monkeypatch.setattr(window, "_lock_screen", lambda: called.append(1))
    setattr(window, flag, True)
    window._check_idle()
    assert not called


# ─── 4. restore: UnlockDialog не должна блокироваться idle'ом ────────────────

def test_open_and_validate_after_restore_sets_unlocking(window, monkeypatch):
    seen = []

    def fake_open(parent=None):
        seen.append(window._unlocking)
        return False
    monkeypatch.setattr(window, "_open_database", fake_open)
    assert window._open_and_validate_after_restore() is False
    assert seen == [True]
    assert window._unlocking is False


# ─── 6. Отказ разблокировки завершает приложение штатно ──────────────────────

def test_unlock_cancel_exits_cleanly(window, monkeypatch):
    _encrypted_window(window, monkeypatch)
    monkeypatch.setattr(window, "_open_database", lambda parent=None: False)
    exits = []
    monkeypatch.setattr(QApplication, "exit", staticmethod(lambda c=0: exits.append(c)))
    closed = []
    monkeypatch.setattr(window, "close", lambda: closed.append(1))
    _make_leaf(window)
    window._lock_screen()
    assert exits == [0]
    assert not closed                         # не зависим от veto в closeEvent


def test_startup_aborted_from_schema_check_exits_cleanly(window, monkeypatch):
    from hranilka.ui.db_lifecycle import StartupAborted
    _encrypted_window(window, monkeypatch)

    def abort():
        raise StartupAborted(1, "схема новее")
    monkeypatch.setattr(window, "_create_tables_or_exit", abort)
    exits = []
    monkeypatch.setattr(QApplication, "exit", staticmethod(lambda c=0: exits.append(c)))
    _make_leaf(window)
    window._lock_screen()                     # SystemExit не должен вылететь
    assert exits == [1]


# ─── 7. run_exclusive_busy ───────────────────────────────────────────────────

def test_run_exclusive_busy_ok_and_error(window):
    ok, res = window.vault.run_exclusive_busy(lambda: 42, "t")
    assert (ok, res) == (True, 42)

    def bad():
        raise ValueError("сбой")
    ok, res = window.vault.run_exclusive_busy(bad, "t")
    assert ok is False and "сбой" in res
    assert window.vault._vault_locked is False
    assert window.vault._busy_dlg is None


def test_run_exclusive_busy_dialog_closed_without_accept(window, monkeypatch):
    """exec() вернулся, а работа ещё идёт: KeyError нет, монополия удерживается,
    пока операция не завершится."""
    import threading
    from hranilka.ui import vault_controller as vc
    release = threading.Event()
    monkeypatch.setattr(vc._BusyDialog, "exec", lambda self: 0)
    ok, res = window.vault.run_exclusive_busy(lambda: release.wait(5), "t")
    assert ok is False
    assert window.vault._vault_locked is True
    release.set()
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(20)

    def check():
        if not window.vault._vault_locked:
            loop.quit()
    timer.timeout.connect(check)
    timer.start()
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    timer.stop()
    assert window.vault._vault_locked is False


def test_busy_late_signal_ignored(window):
    window.vault._busy_dlg = None
    window.vault._on_busy_finished(999)       # не падает без диалога


# ─── 8. wait_idle: вложенность ───────────────────────────────────────────────

def test_wait_idle_nested(window):
    v = window.vault
    v._write_busy = True
    v._write_pending = False
    v.db._dirty = False       # иначе _on_vault_written запустит реальную запись
    results = {}

    def inner():
        results["inner"] = v.wait_idle(timeout_ms=3000)

    def finish():
        v._write_busy = False
        v._on_vault_written(True, "", False)

    QTimer.singleShot(50, inner)       # вложится в loop внешнего wait_idle
    QTimer.singleShot(300, finish)
    results["outer"] = v.wait_idle(timeout_ms=3000)
    assert results == {"inner": True, "outer": True}
    assert v._idle_loops == []


def test_wait_idle_timeout_returns_false_and_cleans(window):
    v = window.vault
    v._write_busy = True
    try:
        assert v.wait_idle(timeout_ms=50) is False
        assert v._idle_loops == []
    finally:
        v._write_busy = False


# ─── 9. Конфликт: один диалог ────────────────────────────────────────────────

def test_conflict_dialog_not_stacked(window, monkeypatch):
    v = window.vault
    asked = []

    def confirm(*a, **k):
        asked.append(1)
        # Пока диалог открыт, приходят таймерный flush и второй конфликт.
        v.db._dirty = True
        v.flush()
        v._on_vault_written(False, "conflict", True)
        return False
    monkeypatch.setattr("hranilka.ui.theme.themed_confirm", confirm)
    monkeypatch.setattr(v.db, "encrypted", True, raising=False)
    started = []
    monkeypatch.setattr(v, "_start_vault_write",
                        lambda force=False: started.append(force))
    v._on_vault_written(False, "conflict", True)
    assert asked == [1]
    assert started == []                      # flush не стартовал запись
    assert v._write_pending is True
    assert v._conflict_dialog_open is False


# ─── 10. closeEvent: сбой сохранения оставляет БД открытой ───────────────────

def test_close_event_failed_save_keeps_db_usable(window, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    window.db.encrypted = True
    window.db._dirty = False        # flush не должен запускать реальную запись

    def boom(persist=True, force=False):
        raise OSError("диск")
    monkeypatch.setattr(window.db, "close", boom)
    monkeypatch.setattr("hranilka.ui.theme.themed_confirm", lambda *a, **k: False)
    ev = QCloseEvent()
    window.closeEvent(ev)
    assert not ev.isAccepted()
    assert window._closing is False           # idle-блокировка снова разрешена
    assert window.db.add_account(None, "после") is not None   # БД жива
    window.db.encrypted = False


# ─── 11. theme._bevel_mode ───────────────────────────────────────────────────

def test_bevel_mode_uses_normalize_chrome():
    from hranilka.ui import theme
    from hranilka.ui.retro_style import CHROME_STYLES

    class C:
        def __init__(self, v):
            self.v = v

        def get(self, k, d=None):
            return self.v
    assert theme._bevel_mode(C("нет-такого")) is False     # = flat
    assert theme._bevel_mode(C(None)) is False
    assert theme._bevel_mode(C("flat")) is False
    non_flat = [c for c in CHROME_STYLES if c != "flat"]
    assert non_flat and all(theme._bevel_mode(C(c)) for c in non_flat)


# ─── Ревью-фиксы: снимок UI, пред-проверка фона, выход, буфер ────────────────

def _tree_state(window):
    expanded, sel = set(), None
    for it in window._iter_items():
        node = window._node(it)
        if node["type"] == "folder" and it.isExpanded():
            expanded.add(node["id"])
    cur = window._node(window.tree.currentItem())
    if cur:
        sel = (cur["type"], cur["id"])
    return expanded, sel


def _two_folders(window):
    f1, f2 = window.db.add_folder("Открытая"), window.db.add_folder("Закрытая")
    a1 = window.db.add_account(None, "Секретный акк", folder_id=f1)
    window.db.add_account(None, "Другой", folder_id=f2)
    window._reload_tree()
    for it in window._iter_items():
        node = window._node(it)
        if node["type"] == "folder":
            it.setExpanded(node["id"] == f1)
    return f1, f2, a1


def test_lock_unlock_restores_expanded_and_selection(window, monkeypatch):
    _encrypted_window(window, monkeypatch)
    f1, f2, a1 = _two_folders(window)
    for it in window._iter_items():
        node = window._node(it)
        if node["type"] == "account" and node["id"] == a1:
            window.tree.setCurrentItem(it)
    before = _tree_state(window)
    assert f1 in before[0] and f2 not in before[0] and before[1][1] == a1
    window._lock_screen()
    assert window._lock_ui_snapshot is None          # снимок одноразовый
    assert _tree_state(window) == before


def test_lock_unlock_restores_search(window, monkeypatch):
    seen = {}

    def fake_open(parent=None):
        seen["snap_has_names"] = "Секретный" in repr(window._lock_ui_snapshot)
        seen["box"] = window.search_box.text()
        return True
    _encrypted_window(window, monkeypatch)
    monkeypatch.setattr(window, "_open_database", fake_open)
    _two_folders(window)
    window.search_box.setText("секрет")
    window.on_search_changed("секрет")
    window._lock_screen()
    assert seen == {"snap_has_names": False, "box": ""}   # пока заперто — пусто
    assert window.search_box.text() == "секрет"
    assert window.search_text == "секрет"
    assert window._lock_ui_snapshot is None


def test_lock_precheck_timeout_keeps_modal_open(window, monkeypatch):
    from hranilka.ui.theme import ThemedDialog
    locked = []
    _encrypted_window(window, monkeypatch,
                      lock_impl=lambda force=False: locked.append(1))
    monkeypatch.setattr(window.vault, "wait_idle", lambda *a, **k: False)
    dlg = ThemedDialog(window.config, window)
    _show_modal(dlg)
    window._lock_screen()
    assert dlg.isVisible() and not locked          # ввод в диалоге не потерян
    dlg.hide()


def test_failed_lock_does_not_quiesce_card(window, monkeypatch):
    def boom(force=False):
        raise OSError("нет места")
    _encrypted_window(window, monkeypatch, lock_impl=boom)
    monkeypatch.setattr("hranilka.ui.theme.themed_info", lambda *a, **k: None)
    gen = window._card_gen
    window._lock_screen()
    assert window._card_gen == gen
    _encrypted_window(window, monkeypatch)
    window._lock_screen()
    assert window._card_gen > gen


def test_lock_deferred_while_vault_quiescing(window, monkeypatch):
    locked = []
    _encrypted_window(window, monkeypatch,
                      lock_impl=lambda force=False: locked.append(1))
    window.vault._quiescing = True
    window._lock_screen()
    assert not locked


def test_quiescing_flag_set_during_quiesce_and_always_reset(window, monkeypatch):
    v = window.vault
    seen = []

    def spy():
        seen.append(v._quiescing)
        return True
    monkeypatch.setattr(v, "_quiesce", spy)
    assert v.run_exclusive(lambda: 1) == (True, 1)
    assert seen == [True] and v._quiescing is False

    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(v, "_quiesce", boom)
    with pytest.raises(RuntimeError):
        v.run_exclusive(lambda: 1)
    assert v._quiescing is False
    monkeypatch.setattr(v, "_quiesce", lambda: False)
    assert v.run_exclusive(lambda: 1)[0] is False
    assert v._quiescing is False


def test_exit_app_clears_clipboard_and_saves_geometry(window, monkeypatch):
    from hranilka.ui.chrome import copy_secret_to_clipboard
    window.config.set("clipboard_clear_on_exit", True)
    window.config.set("remember_geometry", True)
    monkeypatch.setattr(QApplication, "exit", staticmethod(lambda c=0: None))
    copy_secret_to_clipboard("s3cret-clip")
    assert QApplication.clipboard().text() == "s3cret-clip"
    window._exit_app(0)
    assert QApplication.clipboard().text() == ""
    assert window.config.get("_window_geometry")
    window._finish_ui_state()                      # идемпотентно


def test_image_clipboard_cleared_only_if_ours(window):
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QImage, QColor
    from hranilka.ui.chrome import copy_mime_protected
    window.config.set("clipboard_clear_secs", 30)
    img = QImage(4, 4, QImage.Format.Format_RGB32)
    img.fill(QColor("red"))
    cb = QApplication.clipboard()

    mime = QMimeData()
    mime.setImageData(img)
    copy_mime_protected(mime)
    assert window._clip_timer.isActive()
    window._clear_clipboard()                      # наша картинка — стираем
    md = cb.mimeData()
    assert md is None or not md.hasImage()

    mime = QMimeData()
    mime.setImageData(img)
    copy_mime_protected(mime)
    other = QMimeData()                            # пользователь скопировал своё
    other.setImageData(img)
    cb.setMimeData(other)
    window._clear_clipboard()
    assert cb.mimeData().hasImage()                # чужое содержимое цело
    window._clip_timer.stop()


def test_session_lock_retries_while_vault_busy(window, monkeypatch):
    # Событие блокировки сессии одноразовое: занятое хранилище не отменяет его.
    armed = []
    from hranilka.ui import chrome
    monkeypatch.setattr(chrome, "QTimer", SimpleNamespace(
        singleShot=lambda ms, fn: armed.append((ms, fn))))
    monkeypatch.setattr(window, "_lock_screen", lambda: armed.append("locked"))
    monkeypatch.setattr(window, "_any_card_open", lambda: True)
    window.vault._vault_locked = True
    try:
        window._lock_screen_on_session()
    finally:
        window.vault._vault_locked = False
    assert armed == [(1000, window._lock_screen_on_session)]
    window._lock_screen_on_session()
    assert armed[-1] == "locked"
