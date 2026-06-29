"""Сборка MainWindow и проводка mixin'ов (Эпик 4.4).

Постоянная версия headless-smoke: строит настоящий MainWindow offscreen на
ВРЕМЕННОЙ БД (не трогая рабочие hranilka.db/config.json), проверяет, что вынесенные
в примеси методы (дерево, карточка, оболочка, хоткеи) доступны и работают, и
аккуратно снимает instance-lock и поток записи.
"""
import pytest

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    import main
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


def test_restore_plaintext_backup_no_winerror(window, tmp_path):
    """Баг 1: восстановление обычной (незашифрованной) БД из бэкапа. Соединение
    закрывается ДО замены файла, поэтому os.replace не падает с WinError 5."""
    import backup as bk
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
