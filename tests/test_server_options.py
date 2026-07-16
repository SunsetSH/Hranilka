"""Этап 5 VPS-серверов: тумблер show_servers на вкладке «Опции», динамическая
раскладка кнопок создания (MainWindow._relayout_create_buttons), паритет
корзины. См. docs/ТЗ_VPS_Серверы.md §4; образец — tests/test_fin_options.py,
tests/test_server_ui.py.

MainWindow строится offscreen на временной БД. util.fire без работающего
qasync-loop выполняет корутины синхронно, поэтому прямой _select_node
загружает карточку до конца.
"""
import pytest
from PySide6.QtWidgets import QMenu, QPushButton

from hranilka.core.nodetypes import SERVER


@pytest.fixture
def window(qapp, tmp_path, monkeypatch, dispose_window):
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    win = main.MainWindow()
    win.config.set("welcome_shown", True)
    win.apply_config()
    yield win
    dispose_window(win)


def _settings_dialog(window):
    from hranilka.ui.dialogs import SettingsDialog
    dlg = SettingsDialog(window.config, window)
    dlg.set_db(window.db)
    return dlg


def _layout_button_texts(layout):
    """Тексты видимых кнопок ряда: плоские кнопки ряда 1 + кнопки, вложенные в
    контейнеры fin_buttons_widget/srv_buttons_widget ряда 2."""
    texts = []
    for i in range(layout.count()):
        w = layout.itemAt(i).widget()
        if w is None:
            continue
        if isinstance(w, QPushButton):
            texts.append(w.text().strip())
        else:
            texts.extend(b.text().strip() for b in w.findChildren(QPushButton))
    return texts


# ─── 1. Раскладка рядов кнопок создания (4 комбинации) ─────────────────────

@pytest.mark.parametrize("show_fin,show_servers,row1,row2", [
    (True,  True,  ["+ ПАПКА", "+ СЕРВИС", "+ АККАУНТ"],
                   ["+ КАРТА", "+ КРИПТО", "+ СЕРВЕР"]),
    (True,  False, ["+ ПАПКА", "+ СЕРВИС", "+ АККАУНТ"],
                   ["+ КАРТА", "+ КРИПТО"]),
    (False, True,  ["+ ПАПКА", "+ СЕРВИС"],
                   ["+ СЕРВЕР", "+ АККАУНТ"]),
    (False, False, ["+ ПАПКА", "+ СЕРВИС", "+ АККАУНТ"],
                   []),
])
def test_create_buttons_relayout(window, show_fin, show_servers, row1, row2):
    window.config.set("show_fin_instruments", show_fin)
    window.config.set("show_servers", show_servers)
    window.apply_config()

    assert _layout_button_texts(window.row1_layout) == row1
    assert _layout_button_texts(window.row2_layout) == row2
    assert window.row2_widget.isHidden() == (not row2)
    assert window.fin_buttons_widget.isHidden() == (not show_fin)
    assert window.srv_buttons_widget.isHidden() == (not show_servers)


# ─── Стретчи ряда 2 (fin+servers): равная ширина кнопок при растяжении окна ─
#
# QHBoxLayout без явного стретча делит излишек ширины ПОРОВНУ между виджетами
# ряда независимо от числа кнопок внутри — «+ СЕРВЕР» (1 кнопка в своём
# контейнере) становился шире «+ КАРТА»/«+ КРИПТО» (2 кнопки делят тот же
# излишек пополам). Стретч = число кнопок внутри виджета выравнивает прирост
# на кнопку (MainWindow._btn_group_stretch). Стретчи — деталь layout, не
# зависящая от рендера, поэтому проверяемы offscreen без show()/resize().

def test_relayout_fin_and_servers_row2_stretch_matches_button_count(window):
    window.config.set("show_fin_instruments", True)
    window.config.set("show_servers", True)
    window.apply_config()

    layout = window.row2_layout
    widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert widgets == [window.fin_buttons_widget, window.srv_buttons_widget]
    stretches = [layout.stretch(i) for i in range(layout.count())]
    assert stretches == [2, 1]     # 2 кнопки (КАРТА/КРИПТО) : 1 кнопка (СЕРВЕР)


def test_relayout_row1_buttons_have_equal_stretch(window):
    """Ряд 1 — только одиночные кнопки, стретч всегда 1 (регресс-чек: фикс
    #4 не должен был затронуть однокнопочные виджеты)."""
    window.config.set("show_fin_instruments", True)
    window.config.set("show_servers", True)
    window.apply_config()

    layout = window.row1_layout
    stretches = [layout.stretch(i) for i in range(layout.count())]
    assert stretches == [1] * layout.count()


@pytest.mark.parametrize("show_fin,show_servers,expected_attrs,expected_stretch", [
    (True,  False, ["fin_buttons_widget"], [2]),                    # 1 виджет — стретч не влияет
    (False, True,  ["srv_buttons_widget", "add_account_btn"], [1, 1]),
    (False, False, [], []),
])
def test_relayout_other_scenarios_unaffected(window, show_fin, show_servers,
                                             expected_attrs, expected_stretch):
    """Сценарии, где раскладка и так была корректной (только фин / только
    серверы / оба выкл) — фикс #4 их не меняет."""
    window.config.set("show_fin_instruments", show_fin)
    window.config.set("show_servers", show_servers)
    window.apply_config()

    layout = window.row2_layout
    widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert widgets == [getattr(window, name) for name in expected_attrs]
    stretches = [layout.stretch(i) for i in range(layout.count())]
    assert stretches == expected_stretch


# ─── Равная ширина всех 6 кнопок на МИНИМАЛЬНОЙ ширине окна (fin+servers) ──
#
# Стретчи row2_layout выравнивают ширины, только когда есть излишек места
# для распределения. На минимальной ширине окна излишка нет — каждая кнопка
# садится на свой sizeHint (зависит от длины текста), и «+ АККАУНТ»/«+ КАРТА»
# расходятся по ширине (скриншот пользователя). Фикс —
# MainWindow._equalize_create_button_widths выставляет общий minimumWidth
# на все 6 кнопок ряда 1 + ряда 2.

def test_create_buttons_equal_width_at_minimum_window_size(window, qapp):
    window.config.set("show_fin_instruments", True)
    window.config.set("show_servers", True)
    window.apply_config()

    # show() обязателен: без реального top-level показа offscreen-платформа не
    # прогоняет полную активацию сплиттера/вложенных layout'ов на resize()
    # (сплиттер остаётся с устаревшими sizes()) — сама раскладка/стретчи это
    # не затрагивает (см. тесты выше, они умышленно офскрин), но фактическая
    # геометрия виджетов требует show() + несколько processEvents().
    window.show()
    for _ in range(3):
        qapp.processEvents()
    window.resize(window.minimumSizeHint())
    for _ in range(3):
        qapp.processEvents()

    buttons = [window.add_folder_btn, window.add_service_btn, window.add_account_btn,
               *window.fin_buttons_widget.findChildren(QPushButton),
               *window.srv_buttons_widget.findChildren(QPushButton)]
    assert len(buttons) == 6
    widths = [b.width() for b in buttons]
    assert len(set(widths)) == 1, widths
    window.hide()


def test_equalize_create_button_widths_reset_for_other_scenarios(window):
    """Override minimumWidth не должен «протекать» в сценарии, где его не
    просили трогать (переключение fin+servers -> только фин)."""
    window.config.set("show_fin_instruments", True)
    window.config.set("show_servers", True)
    window.apply_config()

    window.config.set("show_servers", False)
    window.apply_config()

    buttons = [window.add_folder_btn, window.add_service_btn, window.add_account_btn,
               *window.fin_buttons_widget.findChildren(QPushButton)]
    assert all(b.minimumWidth() == 0 for b in buttons)


def test_add_account_button_not_duplicated_across_relayouts(window):
    """«+ АККАУНТ» переезжает между рядами, но остаётся тем же объектом (не
    пересоздаётся) — сигналы/хоткеи не отвязываются."""
    btn = window.add_account_btn
    window.config.set("show_fin_instruments", False)
    window.config.set("show_servers", True)
    window.apply_config()
    assert window.add_account_btn is btn
    assert btn in [window.row2_layout.itemAt(i).widget()
                   for i in range(window.row2_layout.count())]

    window.config.set("show_servers", False)
    window.apply_config()
    assert window.add_account_btn is btn
    assert btn in [window.row1_layout.itemAt(i).widget()
                   for i in range(window.row1_layout.count())]


# ─── 2. Дерево и секция карточки аккаунта по флагу (4 комбинации) ─────────

@pytest.mark.parametrize("show_fin,show_servers", [
    (True, True), (True, False), (False, True), (False, False),
])
def test_tree_and_account_section_follow_toggle(window, show_fin, show_servers):
    db = window.db
    sid = db.add_server(None, "Сервер")
    window.config.set("show_fin_instruments", show_fin)
    window.config.set("show_servers", show_servers)
    window.apply_config()

    node = window._find_leaf_item(SERVER, sid)
    assert (node is not None) == show_servers
    assert window.tabs.f_server_linked.isHidden() == (not show_servers)
    assert window.tabs.f_server_linked_heading.isHidden() == (not show_servers)


# ─── 3. Пункт «Создать сервер» в контекстном меню дерева ───────────────────

def test_context_menu_server_item_follows_toggle(window):
    window.config.set("show_servers", False)
    sub = window._add_create_record_menu(QMenu(window))
    assert [a.text() for a in sub.actions()] == ["(i) Аккаунт"]

    window.config.set("show_servers", True)
    sub = window._add_create_record_menu(QMenu(window))
    assert "[#] Сервер" in [a.text() for a in sub.actions()]


def test_context_menu_fin_items_gated_independently_of_servers(window):
    """Фин-пункты подчиняются только show_fin_instruments, серверный пункт —
    только show_servers; тумблеры не влияют друг на друга."""
    window.config.set("show_fin_instruments", True)
    window.config.set("show_servers", False)
    sub = window._add_create_record_menu(QMenu(window))
    texts = [a.text() for a in sub.actions()]
    assert any(t not in ("(i) Аккаунт",) for t in texts)   # фин-пункты есть
    assert "[#] Сервер" not in texts


# ─── 4. Вкладка «Опции»: чекбокс show_servers ───────────────────────────────

def test_options_tab_has_show_servers_check(window):
    dlg = _settings_dialog(window)
    assert dlg.show_servers_check is not None
    snap = dlg._collect_settings()
    assert "show_servers" in snap
    dlg.deleteLater()


def test_disable_servers_warns_and_decline_returns_checkbox(window, monkeypatch):
    from hranilka.ui.dialogs.settings import dialog as dlg_mod
    window.config.set("show_servers", True)
    window.db.add_server(None, "Сервер")
    dlg = _settings_dialog(window)
    assert dlg.show_servers_check.isChecked()
    dlg.show_servers_check.setChecked(False)

    asked = []
    monkeypatch.setattr(dlg_mod, "themed_confirm",
                        lambda *a, **k: asked.append(True) and False)
    assert dlg._resolve_show_servers() is True    # отказ — остаётся включено
    assert asked
    assert dlg.show_servers_check.isChecked()      # чекбокс возвращён

    dlg.show_servers_check.setChecked(False)
    monkeypatch.setattr(dlg_mod, "themed_confirm", lambda *a, **k: True)
    assert dlg._resolve_show_servers() is False    # согласие — выключаем
    dlg.deleteLater()


def test_disable_servers_no_warning_without_records(window, monkeypatch):
    from hranilka.ui.dialogs.settings import dialog as dlg_mod
    window.config.set("show_servers", True)
    dlg = _settings_dialog(window)
    dlg.show_servers_check.setChecked(False)
    monkeypatch.setattr(dlg_mod, "themed_confirm",
                        lambda *a, **k: pytest.fail("не должен спрашивать"))
    assert dlg._resolve_show_servers() is False
    dlg.deleteLater()


def test_count_servers_includes_bin(db):
    assert db.count_servers() == 0
    sid = db.add_server(None, "Сервер")
    db.add_server(None, "Второй")
    db.move_server_to_bin(sid)
    assert db.count_servers() == 2


# ─── 5. Выключение при открытой карточке сервера ────────────────────────────

def test_disable_option_drops_server_drafts_and_closes_card(window, monkeypatch):
    import hranilka.ui.theme as theme_mod
    monkeypatch.setattr(theme_mod, "themed_input", lambda *a, **k: ("Сервер", True))
    window.config.set("show_servers", True)
    window.apply_config()
    window.add_server()
    sid = window._current_server[1]
    window.db.wait_executor_idle()

    window.tree.setCurrentItem(None)
    window._select_node(SERVER, sid)
    window.edit_current()
    window.server_tabs._widgets["hosting"].set_text("Черновик хостинга")
    window.tree.setCurrentItem(None)              # стеш правок сервера
    assert (SERVER, sid) in window._edit_cache
    window._select_node(SERVER, sid)               # карточка снова открыта

    window.config.set("show_servers", False)
    window.apply_config()
    assert window._current_server is None
    assert (SERVER, sid) not in window._edit_cache
    assert (SERVER, sid) not in window._dirty_ids
    assert window.right_stack.currentWidget() is window.placeholder_label


# ─── 6. Корзина: паритет с фин-инструментами ────────────────────────────────

def test_recycle_bin_shows_server_when_off(window):
    from hranilka.ui.dialogs.recycle_bin import RecycleBinDialog
    db = window.db
    sid = db.add_server(None, "Сервер")
    db.move_server_to_bin(sid)
    window.config.set("show_servers", False)
    dlg = RecycleBinDialog(window.config, db, window)
    assert dlg._list.count() == 1
    dlg._list.setCurrentRow(0)
    dlg._restore()
    assert db.get_deleted_count() == 0
    dlg.deleteLater()


def test_restore_server_while_toggle_off_stays_hidden(window):
    """Восстановление сервера при выключенном тумблере не роняет UI: запись
    снова активна в БД, но остаётся невидимой в дереве до включения опции."""
    db = window.db
    sid = db.add_server(None, "Сервер")
    db.move_server_to_bin(sid)
    window.config.set("show_servers", False)
    window.apply_config()

    from hranilka.ui.dialogs.recycle_bin import RecycleBinDialog
    dlg = RecycleBinDialog(window.config, db, window)
    dlg._list.setCurrentRow(0)
    dlg._restore()
    dlg.deleteLater()

    window._reload_tree()
    assert window._find_leaf_item(SERVER, sid) is None
    assert db.count_active_servers() == 1


def test_recycle_bin_empty_clears_servers(window, monkeypatch):
    from hranilka.ui.dialogs import recycle_bin as bin_mod
    db = window.db
    db.move_server_to_bin(db.add_server(None, "Сервер"))
    monkeypatch.setattr(bin_mod, "themed_confirm", lambda *a, **k: True)
    dlg = bin_mod.RecycleBinDialog(window.config, db, window)
    dlg._empty()
    assert db.get_deleted_count() == 0
    assert db.count_servers() == 0
    dlg.deleteLater()


def test_bin_button_counter_includes_servers(window):
    db = window.db
    db.move_server_to_bin(db.add_server(None, "Сервер"))
    window._update_bin_button()
    assert db.get_deleted_count() == 1
    assert not window.title_bar.bin_btn.isHidden()
    assert window.title_bar.bin_btn.text() == "Корзина (1)"


# ─── 7. Экспорт (этап 6): паритет ExportDialog/контекст-меню с фин-листом ──

def test_export_dialog_show_servers_false_hides_and_excludes(window, tmp_path,
                                                              monkeypatch):
    """ExportDialog при show_servers=False: чекбоксы серверов/секретов скрыты,
    Options жёстко include_servers=False/include_server_secrets=False
    (зеркало test_fin_options.test_export_dialog_show_fin_false_...)."""
    from hranilka.ui.dialogs import export_dialog as ed_mod
    from hranilka.services import export as export_mod
    from pathlib import Path
    from PySide6.QtWidgets import QFileDialog
    db = window.db
    db.add_server(None, "Сервер")
    dlg = ed_mod.ExportDialog(window.config, db, None, None, "Т", None, show_servers=False)
    assert dlg._chk_servers.isHidden()
    assert dlg._chk_server_secrets.isHidden()

    captured = {}

    def _fake_html(t, o, p):
        captured["opts"] = o
        Path(p).write_text("")           # write_atomic делает os.replace(p, target)
    monkeypatch.setitem(export_mod.FORMATS, "html", (_fake_html, ".html", "f"))
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(tmp_path / "e.html"), "")))
    monkeypatch.setattr(ed_mod, "themed_info", lambda *a, **k: None)
    dlg._do_export()                      # util.fire без loop — выполняется синхронно
    assert captured["opts"].include_servers is False
    assert captured["opts"].include_server_secrets is False
    dlg.deleteLater()


def test_export_dialog_show_servers_true_visible(window):
    from hranilka.ui.dialogs import export_dialog as ed_mod
    dlg = ed_mod.ExportDialog(window.config, window.db, None, None, "Т", None,
                              show_servers=True)
    assert dlg._chk_servers.text() == "VPS-серверы"
    assert not dlg._chk_servers.isHidden()
    assert dlg._chk_servers.isChecked()              # show_servers=True -> предвыбран
    dlg.deleteLater()


def test_export_dialog_server_secrets_disabled_when_servers_unchecked(window):
    """Снятие «VPS-серверы» деактивирует чекбокс секретов серверов (зеркало
    test_fin_options.test_export_dialog_fin_secrets_disabled_when_fin_unchecked);
    оба чекбокса по умолчанию включены и активны (show_servers=True)."""
    from hranilka.ui.dialogs import export_dialog as ed_mod
    dlg = ed_mod.ExportDialog(window.config, window.db, None, None, "Т", None,
                              show_servers=True)
    assert dlg._chk_servers.isChecked()
    assert dlg._chk_server_secrets.isChecked()
    assert dlg._chk_server_secrets.isEnabled()

    dlg._chk_servers.setChecked(False)
    assert not dlg._chk_server_secrets.isEnabled()

    dlg._chk_servers.setChecked(True)
    assert dlg._chk_server_secrets.isEnabled()
    dlg.deleteLater()


def test_export_all_passes_show_servers(window):
    """export_all/open_export передают show_servers из config (зеркало
    show_fin_instruments)."""
    from hranilka.ui.dialogs import export_dialog as ed_mod
    window.config.set("show_servers", True)
    window.db.add_server(None, "Сервер")
    captured = {}

    class FakeDialog:
        def __init__(self, config, db, node_type, node_id, title, parent=None,
                    show_fin=True, show_servers=False):
            captured["show_servers"] = show_servers

        def exec(self):
            pass

    import hranilka.ui.main_window as main
    orig = main.ExportDialog
    main.ExportDialog = FakeDialog
    try:
        window.export_all()
    finally:
        main.ExportDialog = orig
    assert captured["show_servers"] is True


def test_context_menu_server_has_export_action(window, monkeypatch):
    """ПКМ по серверу в дереве содержит «Экспорт…» (зеркало
    test_fin_ui.test_fin_leaf_context_menu_has_export)."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu
    from hranilka.ui import tree as tree_mod

    window.config.set("show_servers", True)
    window.apply_config()
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()
    item = window._find_leaf_item(SERVER, sid)

    captured = {}

    class RecMenu(QMenu):
        def exec(self, *a, **k):                        # меню не показываем — снимок пунктов
            captured["texts"] = [act.text() for act in self.actions()]
            return None

    monkeypatch.setattr(tree_mod, "QMenu", RecMenu)
    monkeypatch.setattr(window.tree, "itemAt", lambda pos: item)
    window.show_tree_context_menu(QPoint(0, 0))
    assert "Экспорт…" in captured["texts"]
