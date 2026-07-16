"""Итерация 3 фин-сущностей: перекомпоновка дескриптора bank_card, фильтр
пустых строк списков, кнопки создания над деревом, вкладка настроек «Опции»
(show_fin_instruments), скрытие фин-инструментов отовсюду кроме корзины,
фикс NameError ExportDialog, диалог закрытия с типизированным подсчётом.

MainWindow строится offscreen на временной БД (по образцу test_fin_ui.py):
util.fire без работающего qasync-loop выполняет корутины синхронно.
"""
import pytest
from PySide6.QtWidgets import QSizePolicy

from hranilka.core.nodetypes import ACCOUNT, CARD, WALLET
from hranilka.core.fin_types import FIN_TYPES
from hranilka.services import export as export_mod
from hranilka.ui.dialogs.export_dialog import ExportDialog


@pytest.fixture
def window(qapp, tmp_path, monkeypatch, dispose_window):
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    win = main.MainWindow()
    win.config.set("welcome_shown", True)
    win.config.set("show_fin_instruments", True)
    win.apply_config()
    yield win
    dispose_window(win)


# ─── 1. Дескриптор bank_card ──────────────────────────────────────────────────

def test_bank_card_tabs_no_notes_tab():
    """Вкладки без «Заметок»; notes переехало на «Базу»; short_title задан."""
    spec = FIN_TYPES["bank_card"]
    assert spec.tabs == ("База", "Реквизиты", "Счёт", "Банк")
    notes = next(f for f in spec.fields if f.key == "notes")
    assert notes.tab == "База"
    assert spec.short_title == "КАРТА"
    assert FIN_TYPES["crypto_wallet"].short_title == "КРИПТО"


def test_bank_card_row_groups():
    """row_group-ы: тройной "ecp" и "ps" на «Реквизитах», "ibsw"/"innkpp" на
    «Счёте»; поля каждой группы идут подряд (требование раскладки)."""
    spec = FIN_TYPES["bank_card"]
    groups: dict = {}
    for f in spec.fields:
        if f.row_group:
            groups.setdefault(f.row_group, []).append(f)
    assert [f.key for f in groups["ecp"]] == ["expiry", "cvv", "pin"]
    assert [f.key for f in groups["ps"]] == ["payment_system", "card_kind"]
    assert [f.key for f in groups["ibsw"]] == ["iban", "swift"]
    assert [f.key for f in groups["innkpp"]] == ["inn", "kpp"]
    assert all(f.tab == "Реквизиты" for f in groups["ecp"] + groups["ps"])
    assert all(f.tab == "Счёт" for f in groups["ibsw"] + groups["innkpp"])
    # Поля одной группы — подряд в общем списке полей (иначе разъедутся строки).
    keys = [f.key for f in spec.fields]
    for fields in groups.values():
        idx = [keys.index(f.key) for f in fields]
        assert idx == list(range(idx[0], idx[0] + len(idx)))


# ─── 3. Пустые строки списков (kv_list) ───────────────────────────────────────

def _addresses_spec():
    return next(ls for ls in FIN_TYPES["crypto_wallet"].lists
                if ls.key == "addresses")


def test_kv_list_row_with_default_enum_only_is_empty(qapp):
    """Строка с заполненным enum (network="BTC") и пустыми text-полями — пустая,
    не сохраняется; строка с текстом — сохраняется."""
    from hranilka.ui.widgets import KeyValueListWidget
    w = KeyValueListWidget(_addresses_spec().item_fields)
    w.add_row({"network": "BTC", "address": "", "label": ""})
    w.add_row({"network": "ETH", "address": "0xabc", "label": ""})
    items = w.get_items()
    assert len(items) == 1
    assert items[0]["address"] == "0xabc"


def test_fin_save_drops_empty_rows_from_ui(window, monkeypatch):
    """После сохранения кошелька пустые строки исчезают и из интерфейса
    (пересинхронизация виджетов из сохранённого payload)."""
    import hranilka.ui.theme as theme_mod
    monkeypatch.setattr(theme_mod, "themed_input", lambda *a, **k: ("W", True))
    window.add_fin_record("crypto_wallet")
    iid = window._current_fin[1]
    window.db.wait_executor_idle()
    window.tree.setCurrentItem(None)
    window._select_node(WALLET, iid)
    assert window.is_editing

    lw = window.fin_tabs._list_widgets["addresses"]
    lw.set_items([{"network": "ETH", "address": "0xabc", "label": ""}])
    lw.add_row({"network": "BTC"})               # пустая строка с дефолтным enum
    assert len(lw.rows) == 2
    window.save_current()

    payload = window.db.load_fin_item(iid)["payload"]
    assert payload["addresses"] == [
        {"network": "ETH", "address": "0xabc", "label": ""}]
    assert len(lw.rows) == 1                     # пустая строка ушла из UI


# ─── 4. Кнопки над деревом ────────────────────────────────────────────────────

def test_add_account_button_direct_no_menu(window):
    """«+ АККАУНТ» — прямой вызов add_account, без меню; старой кнопки-меню
    создания записи больше нет."""
    assert window.add_account_btn.menu() is None
    assert not hasattr(window, "add_record_btn")


def test_fin_buttons_row_visibility_follows_option(window):
    """Ряд 2 виден при включённой опции и скрыт при выключенной (apply_config)."""
    assert not window.fin_buttons_widget.isHidden()
    window.config.set("show_fin_instruments", False)
    window.apply_config()
    assert window.fin_buttons_widget.isHidden()
    window.config.set("show_fin_instruments", True)
    window.apply_config()
    assert not window.fin_buttons_widget.isHidden()


def test_context_menu_no_fin_types_when_off(window):
    """Контекст-меню «Создать запись» без фин-пунктов при выключенной опции."""
    from PySide6.QtWidgets import QMenu
    window.config.set("show_fin_instruments", False)
    sub = window._add_create_record_menu(QMenu(window))
    texts = [a.text() for a in sub.actions()]
    assert texts == ["(i) Аккаунт"]


# ─── 5. Настройки: вкладка «Опции» ────────────────────────────────────────────

def _settings_dialog(window):
    from hranilka.ui.dialogs import SettingsDialog
    dlg = SettingsDialog(window.config, window)
    dlg.set_db(window.db)
    return dlg


def test_clipboard_clear_field_has_min_width(window):
    """Числовое поле «Очистка буфера» имеет минимальную ширину — на стилях без
    растяжения полей формы значение не сжимается и текст не обрезается.

    Высота — Fixed по sizeHint (не заморожена явным minimumHeight): её
    пересчитывает ThemedDialog._relayout_after_show после того, как система
    применит финальную геометрию окна (см. test_relayout_after_show_*)."""
    from hranilka.ui.dialogs.settings.behavior import _NUM_FIELD_MIN_W
    dlg = _settings_dialog(window)
    assert dlg.clip_clear_secs.minimumWidth() == _NUM_FIELD_MIN_W
    assert _NUM_FIELD_MIN_W >= 100
    assert dlg.clip_clear_secs.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding
    assert dlg.clip_clear_secs.sizePolicy().verticalPolicy() == QSizePolicy.Fixed


def test_relayout_after_show_grows_to_fit_sizehint(qapp, tmp_path, monkeypatch):
    """ThemedDialog._relayout_after_show подтягивает высоту ОКНА к актуальному
    sizeHint. Когда содержимому требуется больше места (у поля вырос
    sizeHint), само окно не увеличивается автоматически — ровно баг с
    коррекцией геометрии системой на некоторых мониторах (см. предупреждение
    QWindowsWindow::setGeometry в логе: запрошенная высота отличается от
    «Resulting geometry», и поля формы оставались сжаты до перетаскивания
    окна)."""
    from hranilka.core import config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui.theme import ThemedDialog
    from PySide6.QtWidgets import QFormLayout, QLineEdit

    cfg = config_mod.Config()
    dlg = ThemedDialog(cfg, None)
    form = QFormLayout()
    field = QLineEdit()
    field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    form.addRow("X:", field)
    dlg.body.addLayout(form)
    dlg.show()
    qapp.processEvents()
    window_before = dlg.height()

    field.setMinimumHeight(field.height() + 40)   # содержимому теперь нужно больше места
    assert dlg.height() == window_before          # окно само не подросло — баг воспроизведён

    dlg._relayout_after_show()
    assert dlg.height() > window_before            # подтянулось под содержимое
    dlg.close()


def test_relayout_after_show_never_shrinks_window(qapp, tmp_path, monkeypatch):
    """_relayout_after_show не уменьшает уже показанное окно ниже текущего
    размера — только подтягивает вверх при нехватке места."""
    from hranilka.core import config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui.theme import ThemedDialog

    cfg = config_mod.Config()
    dlg = ThemedDialog(cfg, None)
    dlg.show()
    qapp.processEvents()

    dlg.resize(dlg.width(), dlg.height() + 200)   # заведомо больше sizeHint
    grown_height = dlg.height()
    dlg._relayout_after_show()
    assert dlg.height() == grown_height
    dlg.close()


def test_options_tab_exists_with_moved_recycle_bin(window):
    """Вкладка «Опции» есть; recycle_bin_check и show_fin_check живут на ней;
    оба ключа собираются в снимок настроек."""
    dlg = _settings_dialog(window)
    labels = [b.text() for b in dlg._tabs.tab_buttons()]
    assert "Опции" in labels
    assert dlg.recycle_bin_check is not None
    assert dlg.show_fin_check is not None
    snap = dlg._collect_settings()
    assert "recycle_bin_enabled" in snap
    assert "show_fin_instruments" in snap
    dlg.deleteLater()


def test_disable_fin_warns_and_decline_returns_checkbox(window, monkeypatch):
    """True→False с фин-записями → подтверждение; отказ возвращает чекбокс в
    True и опция не выключается."""
    from hranilka.ui.dialogs.settings import dialog as dlg_mod
    window.db.add_fin_item(None, "bank_card", "Карта")
    dlg = _settings_dialog(window)
    dlg.show_fin_check.setChecked(False)

    asked = []
    monkeypatch.setattr(dlg_mod, "themed_confirm",
                        lambda *a, **k: asked.append(True) and False)
    assert dlg._resolve_show_fin() is True       # отказ — остаётся включено
    assert asked                                 # предупреждение показано
    assert dlg.show_fin_check.isChecked()        # чекбокс возвращён

    dlg.show_fin_check.setChecked(False)
    monkeypatch.setattr(dlg_mod, "themed_confirm", lambda *a, **k: True)
    assert dlg._resolve_show_fin() is False      # согласие — выключаем
    dlg.deleteLater()


def test_disable_fin_no_warning_without_records(window, monkeypatch):
    """Без фин-записей выключение не спрашивает подтверждения."""
    from hranilka.ui.dialogs.settings import dialog as dlg_mod
    dlg = _settings_dialog(window)
    dlg.show_fin_check.setChecked(False)
    monkeypatch.setattr(dlg_mod, "themed_confirm",
                        lambda *a, **k: pytest.fail("не должен спрашивать"))
    assert dlg._resolve_show_fin() is False
    dlg.deleteLater()


def test_count_fin_items_includes_bin(db):
    """count_fin_items считает все записи, включая корзину."""
    assert db.count_fin_items() == 0
    iid = db.add_fin_item(None, "bank_card", "Карта")
    db.add_fin_item(None, "crypto_wallet", "W")
    db.move_fin_item_to_bin(iid)
    assert db.count_fin_items() == 2


# ─── 6. Скрытие отовсюду, кроме корзины ───────────────────────────────────────

def test_tree_hides_fin_nodes_when_off(window):
    """Дерево без фин-узлов при выключенной опции; включение возвращает их."""
    db = window.db
    iid = db.add_fin_item(None, "bank_card", "Карта")
    window.config.set("show_fin_instruments", False)
    window._reload_tree()
    assert window._find_leaf_item(CARD, iid) is None
    window.config.set("show_fin_instruments", True)
    window._reload_tree()
    assert window._find_leaf_item(CARD, iid) is not None


def test_account_save_keeps_fin_links_when_off(window):
    """КРИТИЧНО: сохранение аккаунта при выключенной опции передаёт
    fin_link_ids=None — существующие связи fin_links не стираются."""
    db = window.db
    sid = db.add_service("S")
    aid = db.add_account(sid, "Акк")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.set_item_links(iid, [aid])

    window.config.set("show_fin_instruments", False)
    window.apply_config()
    window._select_node(ACCOUNT, aid)
    window.edit_current()
    window.tabs.f_name.set_text("Новое имя")
    window.save_current()

    assert db.load_account(aid)["fields"]["account_name"] == "Новое имя"
    assert db.get_item_links(iid) == [aid]       # связь не стёрта


def test_stash_fin_links_none_when_off(window):
    """Стеш правок аккаунта при выключенной опции кладёт fin_links=None —
    сохранение из черновика тоже не тронет связи."""
    db = window.db
    aid = db.add_account(None, "Акк")
    window.config.set("show_fin_instruments", False)
    window.apply_config()
    window._select_node(ACCOUNT, aid)
    window.edit_current()
    window.tabs.f_name.set_text("Правка")
    window.tree.setCurrentItem(None)             # уход → стеш
    assert window._edit_cache[(ACCOUNT, aid)]["fin_links"] is None


def test_disable_option_drops_fin_drafts_and_closes_card(window, monkeypatch):
    """Выключение опции чистит фин-ключи из кеша правок и закрывает открытую
    фин-карточку (правая панель — заглушка)."""
    import hranilka.ui.theme as theme_mod
    monkeypatch.setattr(theme_mod, "themed_input", lambda *a, **k: ("К", True))
    window.add_fin_record("bank_card")
    iid = window._current_fin[1]
    window.db.wait_executor_idle()
    window.tree.setCurrentItem(None)
    window._select_node(CARD, iid)
    window.edit_current()
    window.fin_tabs._widgets["cvv"].set_text("111")
    window.tree.setCurrentItem(None)             # стеш фин-правок
    assert (CARD, iid) in window._edit_cache
    window._select_node(CARD, iid)               # карточка снова открыта

    window.config.set("show_fin_instruments", False)
    window.apply_config()
    assert window._current_fin is None
    assert (CARD, iid) not in window._edit_cache
    assert (CARD, iid) not in window._dirty_ids
    assert window.right_stack.currentWidget() is window.placeholder_label


def test_recycle_bin_shows_fin_when_off(window):
    """Корзина — исключение: фин-записи видны и восстанавливаются при
    выключенной опции."""
    from hranilka.ui.dialogs.recycle_bin import RecycleBinDialog
    db = window.db
    iid = db.add_fin_item(None, "bank_card", "Карта")
    db.move_fin_item_to_bin(iid)
    window.config.set("show_fin_instruments", False)
    dlg = RecycleBinDialog(window.config, db, window)
    assert dlg._list.count() == 1
    dlg._list.setCurrentRow(0)
    dlg._restore()
    assert db.get_deleted_count() == 0
    dlg.deleteLater()


def test_export_dialog_show_fin_false_hides_and_excludes(window, tmp_path,
                                                         monkeypatch):
    """ExportDialog при show_fin=False: чекбоксы фин/секретов скрыты, Options
    жёстко include_fin=False/include_fin_secrets=False."""
    from hranilka.ui.dialogs import export_dialog as ed_mod
    from pathlib import Path
    from PySide6.QtWidgets import QFileDialog
    db = window.db
    db.add_fin_item(None, "bank_card", "Карта")
    dlg = ExportDialog(window.config, db, None, None, "Т", None, show_fin=False)
    assert dlg._chk_fin.isHidden()
    assert dlg._chk_fin_secrets.isHidden()

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
    assert captured["opts"].include_fin is False
    assert captured["opts"].include_fin_secrets is False
    dlg.deleteLater()


def test_export_dialog_show_fin_true_renamed_checkbox(window):
    """При show_fin=True чекбокс переименован в «Финансовые инструменты»."""
    dlg = ExportDialog(window.config, window.db, None, None, "Т", None, show_fin=True)
    assert dlg._chk_fin.text() == "Финансовые инструменты"
    assert not dlg._chk_fin.isHidden()
    dlg.deleteLater()


def test_export_dialog_all_checkboxes_checked_by_default(window):
    """Все чекбоксы «Что включить» по умолчанию ВКЛЮЧЕНЫ (УИ §2026-07-15) —
    в т.ч. критичные секреты фин/серверов, когда сами разделы включены."""
    dlg = ExportDialog(window.config, window.db, None, None, "Т", None,
                       show_fin=True, show_servers=True)
    for chk in (dlg._chk_basic, dlg._chk_other, dlg._chk_gallery,
               dlg._chk_fin, dlg._chk_fin_secrets,
               dlg._chk_servers, dlg._chk_server_secrets):
        assert chk.isChecked(), chk.text()
    assert dlg._chk_fin_secrets.isEnabled()
    assert dlg._chk_server_secrets.isEnabled()
    dlg.deleteLater()


def test_export_dialog_fin_secrets_disabled_when_fin_unchecked(window):
    """Снятие «Финансовые инструменты» деактивирует чекбокс критичных
    секретов; возврат галки — активирует обратно (значение при этом не
    сбрасывается — семантика «не включать» уже следует из include_fin=False)."""
    dlg = ExportDialog(window.config, window.db, None, None, "Т", None, show_fin=True)
    assert dlg._chk_fin.isChecked() and dlg._chk_fin_secrets.isEnabled()

    dlg._chk_fin.setChecked(False)
    assert not dlg._chk_fin_secrets.isEnabled()

    dlg._chk_fin.setChecked(True)
    assert dlg._chk_fin_secrets.isEnabled()
    dlg.deleteLater()


# ─── 7. Фикс NameError ExportDialog в настройках ──────────────────────────────

def test_backup_page_export_all_resolves_and_passes_show_fin(window, monkeypatch):
    """_do_export_all резолвит имя ExportDialog (раньше NameError) и передаёт
    show_fin/show_servers из config."""
    from hranilka.ui.dialogs.settings import backup_page
    calls = {}

    class FakeDialog:
        def __init__(self, config, db, node_type, node_id, title, parent,
                    show_fin=True, show_servers=False):
            calls["show_fin"] = show_fin
            calls["show_servers"] = show_servers

        def exec(self):
            calls["exec"] = True

    monkeypatch.setattr(backup_page, "ExportDialog", FakeDialog)
    dlg = _settings_dialog(window)
    window.config.set("show_fin_instruments", False)
    window.config.set("show_servers", True)
    dlg._do_export_all()
    assert calls == {"show_fin": False, "show_servers": True, "exec": True}
    dlg.deleteLater()


# ─── 9. Диалог закрытия с несохранёнными правками ─────────────────────────────

def test_unsaved_summary_typed_counts(window):
    """Типизированный подсчёт: только ненулевые строки, верные подписи."""
    window._dirty_ids = {(ACCOUNT, 1), (ACCOUNT, 2), (CARD, 3), (WALLET, 4)}
    text = window._unsaved_summary(window._unsaved_keys())
    assert "Аккаунтов: 2" in text
    assert "Банковских карт: 1" in text
    assert "Криптокошельков: 1" in text

    window._dirty_ids = {(CARD, 3)}
    text = window._unsaved_summary(window._unsaved_keys())
    assert "Аккаунтов" not in text
    assert "Банковских карт: 1" in text


def test_close_choice_return_keeps_window(window, monkeypatch):
    """«Вернуться» (и Esc → None) не закрывает окно."""
    import hranilka.ui.theme as theme_mod
    window._dirty_ids = {(ACCOUNT, 1)}
    monkeypatch.setattr(theme_mod, "themed_choice", lambda *a, **k: 2)
    assert window.close() is False               # event.ignore()
    monkeypatch.setattr(theme_mod, "themed_choice", lambda *a, **k: None)
    assert window.close() is False


def test_save_before_exit_writes_account_and_card(window, monkeypatch):
    """«Сохранить и выйти»: стешированные правки аккаунта и карты реально
    пишутся в БД, кеш и dirty-ключи очищаются."""
    import hranilka.ui.theme as theme_mod
    monkeypatch.setattr(theme_mod, "themed_input", lambda *a, **k: ("К", True))
    db = window.db
    aid = db.add_account(None, "Акк")
    window.add_fin_record("bank_card")
    iid = window._current_fin[1]
    db.wait_executor_idle()

    # Правка карты → стеш; правка аккаунта → остаётся редактируемой.
    window.tree.setCurrentItem(None)
    window._select_node(CARD, iid)
    window.edit_current()
    window.fin_tabs._widgets["cvv"].set_text("999")
    window.tree.setCurrentItem(None)             # стеш карты
    window._select_node(ACCOUNT, aid)
    window.edit_current()
    window.tabs.f_name.set_text("Новое имя")     # редактируется сейчас

    assert window._save_unsaved_before_exit() is True
    assert db.load_account(aid)["fields"]["account_name"] == "Новое имя"
    assert db.load_fin_item(iid)["payload"]["cvv"] == "999"
    assert not window._edit_cache
    assert not window._dirty_ids


def test_close_save_error_keeps_window(window, monkeypatch):
    """Ошибка сохранения при «Сохранить и выйти» → themed-ошибка, окно не
    закрывается, черновик не потерян."""
    import hranilka.ui.theme as theme_mod
    window._edit_cache[(ACCOUNT, 777)] = {
        "storage": {"fields": {"account_name": "Сломанный"}}, "links": []}
    window._dirty_ids.add((ACCOUNT, 777))

    def boom(*a, **k):
        raise RuntimeError("disk error")

    monkeypatch.setattr(window.db, "save_account_with_links", boom)
    shown = []
    monkeypatch.setattr(theme_mod, "themed_info",
                        lambda *a, **k: shown.append(a))
    monkeypatch.setattr(theme_mod, "themed_choice", lambda *a, **k: 0)
    assert window.close() is False               # выход отменён
    assert shown                                 # ошибка показана
    assert (ACCOUNT, 777) in window._edit_cache  # черновик цел
