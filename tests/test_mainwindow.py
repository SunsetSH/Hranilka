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
def window(qapp, tmp_path, monkeypatch, dispose_window):
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    win = main.MainWindow()
    yield win
    dispose_window(win)


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


def test_save_from_empty_tab_returns_to_filled_account_tab(window):
    """Save не оставляет стек на скрывшейся пустой вкладке (чёрный экран)."""
    aid = window.db.add_account(None, "Акк")
    window._reload_tree()
    window._select_node(ACCOUNT, aid)
    window.toggle_edit_mode()

    recovery_index = 4
    window.tabs.setCurrentIndex(recovery_index)
    assert window.tabs.currentIndex() == recovery_index
    window.save_current()

    assert not window.is_editing
    assert not window.tabs.isTabVisible(recovery_index)
    assert window.tabs.currentIndex() == 0
    assert window.tabs.f_name.get_text() == "Акк"


def test_account_empty_name_blocks_save_and_opens_base(window, monkeypatch):
    import hranilka.ui.theme as theme_mod

    aid = window.db.add_account(None, "Акк")
    window._reload_tree()
    window._select_node(ACCOUNT, aid)
    window.toggle_edit_mode()
    window.tabs.setCurrentIndex(4)
    window.tabs.f_name.set_text("   ")
    shown = []
    monkeypatch.setattr(
        theme_mod, "themed_info", lambda *a, **k: shown.append(a))

    window.save_current()

    assert window.db.load_account(aid)["fields"]["account_name"] == "Акк"
    assert window.is_editing
    assert window._card_busy is False
    assert window.tabs.currentIndex() == 0
    assert shown and shown[0][2] == "Не заполнено название"


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


@pytest.mark.parametrize("attr", ["_current_fin", "_current_server"])
def test_idle_check_detects_open_fin_and_server_cards(window, monkeypatch, attr):
    """H-01: раньше _check_idle в plaintext-режиме считал карточку открытой
    только по _current_account_id и молча выходил, если была открыта
    фин-запись/сервер (_current_account_id остаётся None) — секреты
    оставались на экране без ограничения времени. Теперь используется общий
    предикат _any_card_open()."""
    from PySide6.QtCore import QDateTime

    window.config.set("idle_lock_mins", 1)
    setattr(window, attr, ("dummy", 1))        # как будто открыта карточка
    window.is_editing = False
    window._last_activity = QDateTime.currentDateTime().addSecs(-600)

    called = {}
    monkeypatch.setattr(window, "_lock_screen", lambda: called.setdefault("locked", True))
    window._check_idle()
    assert called.get("locked") is True


@pytest.mark.parametrize("kind,editing", [
    ("account", False), ("account", True),
    ("fin", False), ("fin", True),
    ("server", False), ("server", True),
])
def test_idle_lock_hides_and_stashes_every_card_type(window, kind, editing):
    """H-01: idle-lock в plaintext-режиме прячет ЛЮБУЮ открытую карточку —
    аккаунт, фин-запись или VPS-сервер — не только аккаунт (chrome.py
    _check_idle/_lock_screen). В режиме редактирования черновик стэшится
    (правки не теряются, узел получает маркер «НЕ СОХРАНЕНО»), все три
    current-состояния и их data-объекты сбрасываются."""
    from PySide6.QtCore import QDateTime
    from hranilka.core.nodetypes import ACCOUNT, CARD, SERVER

    db = window.db
    window.config.set("idle_lock_mins", 1)
    window.config.set("show_fin_instruments", True)
    window.config.set("show_servers", True)
    window.apply_config()

    if kind == "account":
        rec_id = db.add_account(None, "Акк")
        node_type = ACCOUNT
    elif kind == "fin":
        sid = db.add_service("S")
        rec_id = db.add_fin_item(sid, "bank_card", "Карта")
        node_type = CARD
    else:
        rec_id = db.add_server(None, "Сервер")
        node_type = SERVER

    window._reload_tree()
    window._select_node(node_type, rec_id)

    if editing:
        window.edit_current()
        assert window.is_editing
        if kind == "account":
            window.tabs.f_notes.set_text("ЧЕРНОВИК")
        elif kind == "fin":
            window.fin_tabs._widgets["cvv"].set_text("999")
        else:
            window.server_tabs._widgets["hosting"].set_text("ЧЕРНОВИК")

    window._last_activity = QDateTime.currentDateTime().addSecs(-600)
    window._check_idle()

    # Карточка скрыта, все current-состояния и data-объекты сброшены.
    assert window.right_stack.currentWidget() is window.placeholder_label
    assert window._current_account_id is None
    assert window._current_fin is None
    assert window._current_server is None
    assert window.current_fin_data is None
    assert window.current_server_data is None
    assert not window.is_editing

    if editing:
        key = (node_type, rec_id)
        assert key in window._edit_cache            # черновик не потерян
        assert key in window._dirty_ids
        item = window._find_leaf_item(node_type, rec_id)
        assert "НЕ СОХРАНЕНО" in item.text(0)


def test_lock_screen_encrypted_smoke(window, monkeypatch):
    """Смоук на зашифрованный путь блокировки (H-01 не должен его сломать):
    без несохранённых правок _lock_screen сбрасывает current-состояния и
    маршрутизируется в _lock_vault; при незавершённых правках — откладывает
    блокировку (H6-05), current-состояния НЕ трогает."""
    window.db.encrypted = True
    called = {}
    monkeypatch.setattr(window, "_lock_vault", lambda: called.setdefault("vault", True))

    window._current_account_id = 1
    window.is_editing = False
    window._lock_screen()
    assert called.get("vault") is True
    assert window._current_account_id is None

    # Есть незавершённые правки -> блокировка откладывается, состояние не трогается.
    called.clear()
    window._current_fin = ("card", 2)
    window.is_editing = True
    window._lock_screen()
    assert "vault" not in called
    assert window._current_fin == ("card", 2)
    assert window.is_editing is True


def test_shortcut_edit_account_still_works(window):
    """M-01 регрессия: шорткат «Редактировать» на открытом аккаунте работает
    как раньше (has_target теперь считается через _any_card_open(), но для
    аккаунта поведение не меняется)."""
    db = window.db
    aid = db.add_account(None, "Акк")
    window._reload_tree()
    window._select_node(ACCOUNT, aid)
    assert not window.is_editing

    window._sc_edit_account()
    assert window.is_editing


def test_shortcut_generators_work_on_account(window, monkeypatch):
    """M-01 регрессия: генераторы пароля/ПД по-прежнему срабатывают в режиме
    правки открытого аккаунта (позитивная проверка _current_account_id вместо
    отрицания _current_fin не должна ничего сломать для аккаунта)."""
    db = window.db
    aid = db.add_account(None, "Акк")
    window._reload_tree()
    window._select_node(ACCOUNT, aid)
    window.edit_current()
    assert window.is_editing

    called = {"password": False, "personal": False}
    monkeypatch.setattr(window, "generate_password",
                        lambda: called.__setitem__("password", True))
    monkeypatch.setattr(window, "generate_personal_data",
                        lambda: called.__setitem__("personal", True))

    window._sc_gen_password()
    window._sc_gen_personal()
    assert called["password"] is True
    assert called["personal"] is True


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


# ─── H-02: сбой чтения fin_links/server_links в orphan-черновике аккаунта ──
# (симметрично серверам/фин-записям, tests/test_server_ui.py/test_fin_ui.py)

def test_orphan_draft_fin_and_server_links_unknown_on_read_failure(window, monkeypatch):
    """Если чтение fin_links/server_links падает во время построения
    черновика осиротевшей загрузки, черновик получает None («неизвестно»),
    а НЕ [] («снять все связи») — иначе автосохранение при выходе стёрло бы
    реальные привязки аккаунта."""
    db = window.db
    window.config.set("show_fin_instruments", True)
    window.config.set("show_servers", True)

    aid = db.add_account(None, "Акк")
    iid = db.add_fin_item(None, "bank_card", "Карта")
    db.set_item_links(iid, [aid])
    sid = db.add_server(None, "Сервер")
    db.set_server_links(sid, [aid])

    window._current_account_id = None            # мы уже не на этой карточке
    window._edit_cache.pop((ACCOUNT, aid), None)

    def boom(*_a, **_k):
        raise RuntimeError("database is locked")

    with monkeypatch.context() as m:
        m.setattr(db, "get_account_fin_links", boom)
        m.setattr(db, "get_account_server_links", boom)
        window._on_gallery_orphan_upload(aid, "скрин", b"\x89PNGfake")

    assert (ACCOUNT, aid) in window._edit_cache
    cached = window._edit_cache[(ACCOUNT, aid)]
    assert cached["fin_links"] is None
    assert cached["server_links"] is None

    assert window._save_unsaved_before_exit() is True
    assert db.get_item_links(iid) == [aid]        # fin_links НЕ стёрты
    assert db.get_server_links(sid) == [aid]      # server_links НЕ стёрты


def test_open_account_orphan_draft_with_unknown_links_refetches_and_preserves(
        window):
    """Черновик с fin_links=server_links=None (сконструирован напрямую):
    обычное ОТКРЫТИЕ карточки аккаунта должно перечитать связи из БД, а не
    показать пустые списки. Последующее ручное сохранение не стирает
    исходные fin_links/server_links (симметрично серверам/записям,
    tests/test_server_ui.py, tests/test_fin_ui.py)."""
    from hranilka.core.nodetypes import ACCOUNT

    db = window.db
    window.config.set("show_fin_instruments", True)
    window.config.set("show_servers", True)

    aid = db.add_account(None, "Акк")
    iid = db.add_fin_item(None, "bank_card", "Карта")
    db.set_item_links(iid, [aid])
    sid = db.add_server(None, "Сервер")
    db.set_server_links(sid, [aid])
    window._reload_tree()

    window._edit_cache[(ACCOUNT, aid)] = {
        "storage": db.load_account(aid), "links": [],
        "fin_links": None, "server_links": None}
    window._dirty_ids.add((ACCOUNT, aid))

    window._select_node(ACCOUNT, aid)

    cached = window._edit_cache[(ACCOUNT, aid)]
    assert cached["fin_links"] == [iid]
    assert cached["server_links"] == [sid]

    window.save_current()

    assert db.get_item_links(iid) == [aid]        # fin_links НЕ стёрты
    assert db.get_server_links(sid) == [aid]      # server_links НЕ стёрты


def test_open_account_orphan_draft_with_unknown_links_fails_closed_on_second_error(
        window, monkeypatch):
    """Если и повторное чтение fin_links/server_links при открытии черновика
    падает — карточка НЕ открывается (fail-closed), а не молча показывает []."""
    from hranilka.core.nodetypes import ACCOUNT
    import hranilka.ui.theme as theme_mod

    db = window.db
    window.config.set("show_fin_instruments", True)
    window.config.set("show_servers", True)

    aid = db.add_account(None, "Акк")
    iid = db.add_fin_item(None, "bank_card", "Карта")
    db.set_item_links(iid, [aid])
    sid = db.add_server(None, "Сервер")
    db.set_server_links(sid, [aid])
    window._reload_tree()

    window._edit_cache[(ACCOUNT, aid)] = {
        "storage": db.load_account(aid), "links": [],
        "fin_links": None, "server_links": None}
    window._dirty_ids.add((ACCOUNT, aid))

    def boom(*_a, **_k):
        raise RuntimeError("database is locked")

    with monkeypatch.context() as m:
        # _show_card_error зовёт модальный themed_info (d.exec) — гасим.
        m.setattr(theme_mod, "themed_info", lambda *a, **k: None)
        m.setattr(db, "get_account_fin_links", boom)
        window._select_node(ACCOUNT, aid)

    assert window._current_account_id is None      # карточка не открыта
    cached = window._edit_cache[(ACCOUNT, aid)]
    assert cached["fin_links"] is None              # черновик цел
    assert db.get_item_links(iid) == [aid]          # связь в БД не тронута
    assert db.get_server_links(sid) == [aid]


# ─── H-07: fail-closed при повреждённом файле БД ──────────────────────────────

def test_corrupt_db_fail_closed_offers_recovery(qapp, tmp_path, monkeypatch):
    """Мусор вместо hranilka.db: не сырой DatabaseError, а recovery-предложение;
    при отказе — корректный выход, в подозрительный файл ничего не записано."""
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    garbage = b"not-a-sqlite-database" * 100
    (tmp_path / "hranilka.db").write_bytes(garbage)

    offered = {"n": 0}

    def fake_offer(self, err, parent=None):
        offered["n"] += 1
        return False                        # пользователь выбрал «Выход»

    monkeypatch.setattr(main.MainWindow, "_offer_corrupt_recovery", fake_offer)
    with pytest.raises(SystemExit):
        main.MainWindow()
    assert offered["n"] == 1
    assert (tmp_path / "hranilka.db").read_bytes() == garbage


def test_corrupt_db_recovery_restores_and_opens(qapp, tmp_path, monkeypatch, dispose_window):
    """Восстановление из валидного бэкапа после обнаружения порчи: окно
    открывается, повреждённый файл отложен рядом (*.corrupt-*)."""
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)

    from hranilka.data.database import Database
    backup_path = tmp_path / "backup.db"
    bdb = Database(str(backup_path))
    bdb.connect()
    bdb.create_tables()
    bdb.close()
    bdb.shutdown_executor()

    (tmp_path / "hranilka.db").write_bytes(b"garbage-not-sqlite" * 64)

    def fake_offer(self, err, parent=None):
        # Как do_restore в _offer_corrupt_recovery, но без модальных диалогов.
        from hranilka.services import backup as bk
        self._archive_db_file("corrupt")
        bk.restore_backup(str(backup_path), self.db.db_path)
        return True

    monkeypatch.setattr(main.MainWindow, "_offer_corrupt_recovery", fake_offer)
    win = main.MainWindow()
    try:
        assert win.db.conn is not None
        assert list(tmp_path.glob("hranilka.db.corrupt-*"))
    finally:
        dispose_window(win)


def test_busy_db_not_treated_as_corrupt(qapp, tmp_path, monkeypatch):
    """SQLITE_BUSY (внешний BEGIN EXCLUSIVE на здоровой БД) — не порча:
    recovery с заменой бэкапом НЕ предлагается, файл не тронут."""
    import sqlite3
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)

    # Здоровая БД Хранилки.
    from hranilka.data.database import Database
    db_path = tmp_path / "hranilka.db"
    d = Database(str(db_path))
    d.connect()
    d.create_tables()
    d.close()
    d.shutdown_executor()

    # Внешний процесс держит эксклюзивную блокировку.
    holder = sqlite3.connect(str(db_path))
    holder.execute("BEGIN EXCLUSIVE")

    calls = {"corrupt": 0, "confirm": 0}
    monkeypatch.setattr(
        main.MainWindow, "_offer_corrupt_recovery",
        lambda self, e, parent=None: calls.__setitem__("corrupt", calls["corrupt"] + 1) or False)

    def fake_confirm(cfg, parent, title, text):
        calls["confirm"] += 1
        assert "заблокирован" in text
        return False                         # пользователь не повторяет — выход
    monkeypatch.setattr(main.theme, "themed_confirm", fake_confirm)

    original = db_path.read_bytes()
    try:
        with pytest.raises(SystemExit):
            main.MainWindow()
    finally:
        holder.rollback()
        holder.close()
    assert calls["corrupt"] == 0, "BUSY не должен вести в recovery"
    assert calls["confirm"] == 1, "BUSY предлагает повтор"
    assert db_path.read_bytes() == original


def test_archive_db_file_returns_path_and_raises(window, monkeypatch, tmp_path):
    """_archive_db_file возвращает путь; OSError переименования пробрасывается
    (вызыватель обязан отменить восстановление)."""
    import os
    # В реальном recovery-потоке соединение закрыто ДО архивирования
    # (на Windows нельзя переименовать открытый файл).
    window.db.close(persist=False)
    archived = window._archive_db_file("corrupt")
    assert archived and ".corrupt-" in archived
    assert list(tmp_path.glob("hranilka.db.corrupt-*"))
    # Вернуть файл и соединение, чтобы teardown фикстуры прошёл штатно.
    os.replace(archived, window.db.db_path)

    def boom(src, dst):
        raise OSError("нет прав")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        window._archive_db_file("corrupt")
    monkeypatch.undo()
    window.db.connect()


def test_save_unblocks_card_on_stale_session(window, monkeypatch):
    """StaleSessionError во время Save (смена сессии БД: restore, вкл/выкл
    шифрования) не оставляет карточку заблокированной: busy снят, поля
    editable, черновик — в кеше правок."""
    from hranilka.data.database import StaleSessionError

    db = window.db
    aid = db.add_account(None, "Акк")
    window._reload_tree()
    window._select_node("account", aid)
    window.toggle_edit_mode()
    window.tabs.f_login.set_text("user@example.com")
    window.tabs.setCurrentIndex(4)  # пустая вкладка скрывается на время записи

    def boom(*a, **k):
        raise StaleSessionError()
    monkeypatch.setattr(db, "save_account_with_links", boom)

    window.save_account()
    assert window._card_busy is False
    assert not window.tabs.f_login.input.isReadOnly()
    assert ("account", aid) in window._edit_cache
    assert ("account", aid) in window._dirty_ids
    assert window.tabs.currentIndex() == 4
