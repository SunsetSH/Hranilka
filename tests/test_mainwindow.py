"""Сборка MainWindow и проводка mixin'ов (Эпик 4.4).

Постоянная версия headless-smoke: строит настоящий MainWindow offscreen на
ВРЕМЕННОЙ БД (не трогая рабочие hranilka.db/config.json), проверяет, что вынесенные
в примеси методы (дерево, карточка, оболочка, хоткеи) доступны и работают, и
аккуратно снимает instance-lock и поток записи.
"""
import pytest

from hranilka.core.nodetypes import ACCOUNT

# Общий session-qapp живёт в conftest.py (M-16).


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    win = main.MainWindow()
    yield win
    win.vault.shutdown()
    win._instance_lock.release()


def test_window_builds_and_mixins_wired(window):
    # Методы из всех примесей доступны на одном объекте (MRO).
    for name in ("_reload_tree", "add_folder", "on_item_selected",
                 "load_data_to_ui", "_setup_shortcuts", "_run_shortcut",
                 "eventFilter", "_apply_screenshot_protect", "toggle_maximize"):
        assert callable(getattr(window, name)), name
    assert window._shortcuts, "хоткеи созданы при сборке"


def test_tree_build_and_navigation(window):
    db = window.db
    fid = db.add_folder("Папка")
    sid = db.add_service("Сервис", fid)
    aid = db.add_account(sid, "Акк")
    window._reload_tree()
    # Узел аккаунта находится и выбирается через навигацию (TreeMixin).
    window._select_node("account", aid)
    node = window._node(window.tree.currentItem())
    assert node and node["type"] == "account" and node["id"] == aid


def test_vault_gate_runs_on_window(window):
    ok, res = window.vault.run_exclusive(lambda: "готово")
    assert ok and res == "готово"


def test_chrome_methods_no_crash(window):
    window._on_field_copied()
    window._apply_screenshot_protect(False)
    window._restore_geometry()


def test_field_copied_arms_clear_timer(window):
    """При clipboard_clear_secs>0 _on_field_copied взводит таймер авто-очистки
    буфера; по срабатыванию таймера буфер очищается (ui_chrome.py:54-66)."""
    from PySide6.QtWidgets import QApplication

    window.config.set("clipboard_clear_secs", 5)
    QApplication.clipboard().setText("секрет")
    window._on_field_copied()
    assert window._clip_timer.isActive()          # таймер очистки взведён

    # Симулируем срабатывание таймера напрямую (offscreen-safe, без ожидания).
    window._clip_timer.timeout.emit()
    assert QApplication.clipboard().text() == ""  # буфер очищен


def test_field_copied_no_timer_when_disabled(window):
    """При clipboard_clear_secs==0 таймер не взводится."""
    window.config.set("clipboard_clear_secs", 0)
    window._clip_timer.stop()
    window._on_field_copied()
    assert not window._clip_timer.isActive()


def test_idle_check_locks_plaintext_card(window, monkeypatch):
    """_check_idle прячет открытую карточку по простою в обычном режиме
    (ui_chrome.py:114-161). Эмулируем «давно не было активности» подменой
    _last_activity, чтобы не ждать реального таймаута."""
    from PySide6.QtCore import QDateTime

    window.config.set("idle_lock_mins", 1)
    window._current_account_id = 123          # как будто открыта карточка
    window.is_editing = False
    # Последняя активность — 10 минут назад (> порога в 1 мин).
    window._last_activity = QDateTime.currentDateTime().addSecs(-600)

    called = {}
    monkeypatch.setattr(window, "_lock_screen", lambda: called.setdefault("locked", True))
    window._check_idle()
    assert called.get("locked") is True

    # Свежая активность — блокировки нет.
    called.clear()
    window._last_activity = QDateTime.currentDateTime()
    window._check_idle()
    assert "locked" not in called


def test_restore_plaintext_backup_no_winerror(window, tmp_path):
    """Баг 1: восстановление обычной (незашифрованной) БД из бэкапа. Соединение
    закрывается ДО замены файла, поэтому os.replace не падает с WinError 5."""
    from hranilka.services import backup as bk
    db = window.db
    fid = db.add_folder("Папка")
    sid = db.add_service("Сервис", fid)
    db.add_account(sid, "Acc1")
    dest = bk.create_backup(db.db_path, str(tmp_path / "backups"))

    db.add_account(sid, "Acc2")            # изменения ПОСЛЕ бэкапа
    assert len(db.get_all_accounts()) == 2

    ok, err = window._restore_from_backup(str(dest))
    assert ok, err
    accts = window.db.get_all_accounts()   # name — это полный путь до аккаунта
    assert len(accts) == 1                 # вернулись к состоянию бэкапа (Acc2 нет)
    assert accts[0]["name"].endswith("Acc1")


# ─── M7-05: осиротевшая загрузка попадает в черновик правок ───────────────────

def test_orphan_upload_lands_in_edit_cache(window):
    """Загрузка, стартовавшая для аккаунта, к завершению которой пользователь ушёл
    с карточки, дописывается в черновик правок этого аккаунта, а сам аккаунт
    помечается «грязным» (M7-05, путь _edit_cache present)."""
    db = window.db
    sid = db.add_service("Сервис")
    aid = db.add_account(sid, "Acc")

    # У аккаунта уже есть черновик правок (как после ухода в режиме правки).
    window._edit_cache[(ACCOUNT, aid)] = {
        "storage": {"fields": {}, "personal": {}, "questions": [],
                    "recovery": {}, "codes": [], "gallery": []},
        "links": [],
    }
    window._current_account_id = None          # мы уже НЕ на этой карточке

    data = b"\x89PNG_orphan_bytes"
    window._on_gallery_orphan_upload(aid, "подпись", data)

    gallery = window._edit_cache[(ACCOUNT, aid)]["storage"]["gallery"]
    assert len(gallery) == 1
    assert gallery[0]["data"] == data
    assert gallery[0]["desc"] == "подпись"
    assert gallery[0]["image_id"] is None      # новая картинка
    assert (ACCOUNT, aid) in window._dirty_ids  # аккаунт помечен несохранённым


def test_dirty_keys_are_type_id_tuples(window):
    """Ключи _dirty_ids/_edit_cache — кортежи (node_type, id) (Фаза 0): id аккаунта
    и фин-записи с одинаковым числом не сталкиваются. Осиротевшая загрузка —
    доступный из теста путь, помечающий аккаунт грязным."""
    db = window.db
    sid = db.add_service("Сервис")
    aid = db.add_account(sid, "Acc")
    window._current_account_id = None

    window._on_gallery_orphan_upload(aid, "x", b"data")

    assert (ACCOUNT, aid) in window._dirty_ids
    assert all(isinstance(k, tuple) and len(k) == 2 for k in window._dirty_ids)
    assert all(isinstance(k, tuple) and len(k) == 2 for k in window._edit_cache)


def test_orphan_upload_deleted_account_dropped(window):
    """Если аккаунт удалён к моменту завершения загрузки и черновика нет — картинка
    роняется, черновик не создаётся, dirty не помечается (M7-05). _on_gallery_orphan_
    upload запускает _orphan_into_new_draft; без работающего qasync-loop util.fire
    выполняет корутину синхронно до конца."""
    missing_aid = 999999                       # такого аккаунта нет
    window._current_account_id = None
    window._on_gallery_orphan_upload(missing_aid, "x", b"data")

    assert (ACCOUNT, missing_aid) not in window._edit_cache
    assert (ACCOUNT, missing_aid) not in window._dirty_ids


def test_orphan_upload_builds_draft_from_db(window):
    """Черновика ещё нет: _on_gallery_orphan_upload подгружает аккаунт из БД,
    формирует черновик (формат _stash_current_edits) и дописывает картинку (M7-05)."""
    db = window.db
    sid = db.add_service("Сервис")
    aid = db.add_account(sid, "Acc")
    window._current_account_id = None          # мы НЕ на этой карточке
    window._edit_cache.pop((ACCOUNT, aid), None)  # черновика нет

    data = b"\x89PNG_from_db"
    window._on_gallery_orphan_upload(aid, "снимок", data)

    assert (ACCOUNT, aid) in window._edit_cache
    gallery = window._edit_cache[(ACCOUNT, aid)]["storage"]["gallery"]
    assert gallery[-1]["data"] == data
    assert gallery[-1]["desc"] == "снимок"
    assert gallery[-1]["image_id"] is None
    assert isinstance(window._edit_cache[(ACCOUNT, aid)]["links"], list)
    assert (ACCOUNT, aid) in window._dirty_ids
