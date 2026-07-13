"""Фаза 2 фин-сущностей: тип «Криптокошелёк» (дескриптор crypto_wallet,
SeedPhraseWidget, KeyValueListWidget) и связи карта/кошелёк ↔ аккаунт
(концепт §6 и §8): виджеты с обеих сторон, атомарное сохранение, корзина.

UI-тесты строят настоящий MainWindow offscreen на временной БД (по образцу
test_fin_ui.py): util.fire без qasync-loop выполняет корутины синхронно.
"""
import pytest

from PySide6.QtWidgets import QApplication, QLineEdit

from hranilka.core.nodetypes import ACCOUNT, CARD, WALLET
from hranilka.core.fin_types import FIN_TYPES
from hranilka.ui.widgets import (KeyValueListWidget, LinkedFinItemsWidget,
                                 SeedPhraseWidget, fin_item_display)

# 12 валидных слов BIP-39 для тестов seed-фразы.
_SEED12 = ("abandon ability able about above absent absorb abstract "
           "absurd abuse access accident")


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    win = main.MainWindow()
    win.config.set("welcome_shown", True)
    win.config.set("show_fin_instruments", True)
    win.apply_config()
    yield win
    win.vault.shutdown()
    win._instance_lock.release()


# ─── Дескриптор crypto_wallet ─────────────────────────────────────────────────

def test_crypto_wallet_registered():
    spec = FIN_TYPES["crypto_wallet"]
    assert spec.node_type == WALLET
    assert spec.tree_prefix == "[₿] "
    assert spec.tabs == ("Кошелёк", "Seed-фраза", "Адреса", "Ключи", "Заметки")
    assert {ls.key for ls in spec.lists} == {"addresses", "private_keys"}
    # У кошелька нет last4/срока — экстракт-колонки пустые.
    assert spec.extract({"seed_phrase": _SEED12}) == ("", None)
    # Секретный набор: seed, passphrase, ключи (ключи — в ListSpec).
    assert {"seed_phrase", "passphrase"} <= spec.secret_keys()


def test_wallet_tabs_payload_roundtrip(qapp):
    """Round-trip load_payload/collect_payload, включая ListSpec и seed."""
    from hranilka.ui.fin_tabs import FinItemTabs
    tabs = FinItemTabs(FIN_TYPES["crypto_wallet"])
    tabs.set_all_editable(True)
    payload = {
        "wallet_kind": "hardware", "vendor": "Ledger",
        "derivation_path": "m/44'/60'/0'/0/0", "created": "2024-05-01",
        "seed_phrase": _SEED12,
        "passphrase": "extra25", "notes": "заметка",
        "addresses": [
            {"network": "ETH", "address": "0xabc", "label": "основной"},
            {"network": "BTC", "address": "bc1qxyz", "label": ""},
        ],
        "private_keys": [{"label": "eth", "key": "0xdeadbeef"}],
    }
    tabs.load_payload(payload)
    out = tabs.collect_payload()
    for key, value in payload.items():
        assert out[key] == value, key


def test_wallet_tabs_has_gallery_last(qapp):
    """После вкладок дескриптора добавляется общая «Галерея» (последняя).
    Отдельной вкладки «Связи» больше нет — связи внизу первой вкладки (итерация 2)."""
    from hranilka.ui.fin_tabs import FinItemTabs
    for type_id in ("bank_card", "crypto_wallet"):
        tabs = FinItemTabs(FIN_TYPES[type_id])
        labels = [b.text() for b in tabs.tab_buttons()]
        assert labels[-1] == "Галерея"
        assert "Связи" not in labels
        assert tabs.f_linked_accounts is not None


# ─── SeedPhraseWidget ─────────────────────────────────────────────────────────

def test_seed_set_get_roundtrip(qapp):
    w = SeedPhraseWidget()
    w.set_text(_SEED12)
    assert len(w._cells) == 12
    assert w.get_text() == _SEED12


def test_seed_grid_rebuild_on_count_change(qapp):
    w = SeedPhraseWidget()
    w.set_editable(True)
    w.set_text(_SEED12)
    # Комбо 24 → сетка перестраивается, введённые слова сохраняются.
    w.count_combo.setCurrentIndex(w.count_combo.findData(24))
    assert len(w._cells) == 24
    assert w.get_text() == _SEED12              # пустые ячейки не сохраняются
    # 15 слов → минимальный вмещающий размер 15.
    w.set_text(_SEED12 + " actor actress act")
    assert len(w._cells) == 15


def test_seed_hidden_by_default_reveal_with_autohide(qapp):
    w = SeedPhraseWidget()
    w.set_text(_SEED12)
    w.set_editable(False)
    assert all(e.echoMode() == QLineEdit.Password for e, _w, _c in w._cells)
    # «ПОКАЗАТЬ ВСЁ» — слова видны, таймер автоскрытия взведён.
    w.reveal_btn.click()
    assert all(e.echoMode() == QLineEdit.Normal for e, _w, _c in w._cells)
    assert w._hide_timer.isActive()
    # Срабатывание таймера скрывает слова обратно.
    w._hide_timer.timeout.emit()
    assert all(e.echoMode() == QLineEdit.Password for e, _w, _c in w._cells)
    # В правке слова видны (как пароль в режиме правки).
    w.set_editable(True)
    assert all(e.echoMode() == QLineEdit.Normal for e, _w, _c in w._cells)


def test_seed_paste_from_clipboard(qapp):
    w = SeedPhraseWidget()
    w.set_editable(True)
    QApplication.clipboard().setText("abandon\nability  able\tabout")
    w.paste_phrase()
    assert w.get_text() == "abandon ability able about"
    assert len(w._cells) == 12                  # минимальный размер сетки


def test_seed_copy_single_line(qapp):
    w = SeedPhraseWidget()
    w.set_text(_SEED12)
    QApplication.clipboard().clear()
    copied = []
    w.copy_signal.connect(lambda: copied.append(True))
    w.do_copy()
    assert QApplication.clipboard().text() == _SEED12
    assert copied == [True]


def test_seed_bip39_warning(qapp):
    """Не-словарное слово помечается [!]; словарное и пустое — нет."""
    w = SeedPhraseWidget()
    w.set_editable(True)
    w.set_text("abandon qwertyzz")
    edit0, warn0, _c0 = w._cells[0]
    edit1, warn1, _c1 = w._cells[1]
    _e2, warn2, _c2 = w._cells[2]
    assert warn0.isHidden()                     # словарное слово
    assert not warn1.isHidden()                 # не из BIP-39 — предупреждение
    assert warn2.isHidden()                     # пустая ячейка
    edit1.setText("zoo")                        # исправили на словарное
    assert warn1.isHidden()


# ─── KeyValueListWidget ───────────────────────────────────────────────────────

def _addresses_fields():
    spec = FIN_TYPES["crypto_wallet"]
    return next(ls for ls in spec.lists if ls.key == "addresses").item_fields


def _keys_fields():
    spec = FIN_TYPES["crypto_wallet"]
    return next(ls for ls in spec.lists if ls.key == "private_keys").item_fields


def test_kv_list_roundtrip(qapp):
    w = KeyValueListWidget(_addresses_fields())
    items = [{"network": "ETH", "address": "0xabc", "label": "осн"},
             {"network": "TON", "address": "EQxyz", "label": ""}]
    w.set_items(items)
    assert w.get_items() == items


def test_kv_list_add_remove_rows(qapp, monkeypatch):
    w = KeyValueListWidget(_addresses_fields())
    w.set_editable(True)
    w.add_row({"network": "BTC", "address": "bc1q", "label": ""})
    w.add_row()                                  # пустая строка
    assert len(w.rows) == 2
    assert w.get_items() == [{"network": "BTC", "address": "bc1q", "label": ""}]
    # Удаление строки (подтверждение — согласие).
    monkeypatch.setattr("hranilka.ui.widgets.kv_list._confirm",
                        lambda *a: True)
    w.remove_row(w.rows[0].widget)
    assert len(w.rows) == 1
    assert w.get_items() == []


def test_kv_list_secret_masked_and_copy(qapp):
    w = KeyValueListWidget(_keys_fields())
    w.set_items([{"label": "eth", "key": "0xdeadbeef"}])
    w.set_editable(False)
    row = w.rows[0]
    key_edit = row.fields["key"]
    assert key_edit.echoMode() == QLineEdit.Password   # секрет скрыт
    assert not row.secrets[0][1].isHidden()            # reveal-кнопка [*]
    # [КОП] копирует значение строки — секретный ключ, с сигналом.
    QApplication.clipboard().clear()
    copied = []
    w.copy_signal.connect(lambda: copied.append(True))
    w._copy_row(row)
    assert QApplication.clipboard().text() == "0xdeadbeef"
    assert copied == [True]
    # В правке секрет показан, reveal скрыт (как CopyableField).
    w.set_editable(True)
    assert key_edit.echoMode() == QLineEdit.Normal


# ─── Слой данных: связи (§8) ──────────────────────────────────────────────────

def test_list_fin_items_live_only(db):
    i1 = db.add_fin_item(None, "bank_card", "Карта")
    i2 = db.add_fin_item(None, "crypto_wallet", "Кошелёк")
    db.move_fin_item_to_bin(i2)
    items = db.list_fin_items()
    assert [it["id"] for it in items] == [i1]


def test_get_item_link_accounts_live_only(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    a2 = db.add_account(sid, "A2")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.set_item_links(iid, [a1, a2])
    db.move_account_to_bin(a2)
    rows = db.get_item_link_accounts(iid)
    assert [r["id"] for r in rows] == [a1]
    assert "A1" in rows[0]["name"]


def test_save_account_with_fin_links_atomic(db):
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    storage = db.load_account(aid)
    db.save_account_with_links(aid, storage, [], [iid, iid])   # дедуп
    assert db.get_item_links(iid) == [aid]
    db.save_account_with_links(aid, storage, [], [])           # отвязка
    assert db.get_item_links(iid) == []
    # Старый вызов без fin_item_ids связи не трогает.
    db.save_account_with_links(aid, storage, [], [iid])
    db.save_account_with_links(aid, storage, [])
    assert db.get_item_links(iid) == [aid]


def test_links_survive_bin_and_restore(db):
    """Корзина не трогает fin_links; restore возвращает связь на обе стороны."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.set_item_links(iid, [aid])

    db.move_fin_item_to_bin(iid)
    assert db.get_item_links(iid) == [aid]      # строка связи цела
    assert db.get_account_fin_links(aid) == []  # но не показывается
    db.restore_fin_item(iid)
    assert [r["id"] for r in db.get_account_fin_links(aid)] == [iid]


def test_account_save_keeps_links_of_binned_item(db):
    """Сохранение аккаунта, пока запись в корзине, не рвёт её связь (§8)."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.set_item_links(iid, [aid])
    db.move_fin_item_to_bin(iid)
    storage = db.load_account(aid)
    # UI отдаёт только видимые (живые) привязки — пустой список.
    db.save_account_with_links(aid, storage, [], [])
    db.restore_fin_item(iid)
    assert db.get_item_links(iid) == [aid]


def test_item_save_keeps_links_of_binned_account(db):
    """Зеркально: сохранение записи не рвёт связь с аккаунтом в корзине."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.set_item_links(iid, [aid])
    db.move_account_to_bin(aid)
    db.set_item_links(iid, [])                  # видимых связей нет
    db.restore_account(aid)
    assert db.get_item_links(iid) == [aid]


def test_final_delete_cascades_links(db):
    """Окончательное удаление записи рвёт связи (FK CASCADE)."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.set_item_links(iid, [aid])
    db.delete_items([(CARD, iid)], to_bin=False)
    db.cursor.execute("SELECT COUNT(*) AS n FROM fin_links")
    assert db.cursor.fetchone()["n"] == 0


# ─── Виджет LinkedFinItemsWidget ──────────────────────────────────────────────

def test_fin_item_display_prefixes():
    assert fin_item_display({"item_type": "bank_card", "name": "Tinkoff",
                             "card_last4": "1234"}) == "[$] Tinkoff •1234"
    assert fin_item_display({"item_type": "crypto_wallet", "name": "Холодный",
                             "card_last4": None}) == "[₿] Холодный"


def test_linked_fin_items_widget(qapp):
    w = LinkedFinItemsWidget()
    w.set_data([{"id": 7, "item_type": "bank_card", "name": "К",
                 "card_last4": "1234"},
                {"id": 9, "item_type": "crypto_wallet", "name": "W",
                 "card_last4": None}])
    assert w.get_data() == [7, 9]
    fired = []
    w.navigate_requested.connect(lambda nt, iid: fired.append((nt, iid)))
    w.items[1][2].layout().itemAt(0).widget().click()   # кнопка-строка кошелька
    assert fired == [(WALLET, 9)]


# ─── UI: создание кошелька и связи через MainWindow ───────────────────────────

def test_create_record_context_menu_has_fin_types(window):
    """Контекст-меню «Создать запись» строится из реестра: аккаунт + фин-типы
    (старая кнопка-меню заменена на «+ АККАУНТ» и ряд фин-кнопок)."""
    from PySide6.QtWidgets import QMenu
    menu = QMenu(window)
    sub = window._add_create_record_menu(menu)
    texts = [a.text() for a in sub.actions()]
    assert "(i) Аккаунт" in texts
    assert "[₿] Криптокошелёк" in texts
    assert "[$] Банковская карта" in texts


def test_create_wallet_via_mainwindow_saves_seed(window, monkeypatch):
    import hranilka.ui.theme as theme_mod
    monkeypatch.setattr(theme_mod, "themed_input",
                        lambda *a, **k: ("Мой кошелёк", True))
    window.add_fin_record("crypto_wallet")
    assert window._current_fin is not None and window._current_fin[0] == WALLET
    iid = window._current_fin[1]
    db = window.db
    db.wait_executor_idle()

    window.tree.setCurrentItem(None)
    window._select_node(WALLET, iid)
    assert window.is_editing
    assert window.right_stack.currentWidget() is window.fin_tabs_by_type[WALLET]
    assert window.fin_tabs is window.fin_tabs_by_type[WALLET]

    window.fin_tabs._widgets["seed_phrase"].set_text(_SEED12)
    window.fin_tabs._list_widgets["addresses"].set_items(
        [{"network": "ETH", "address": "0xabc", "label": ""}])
    window.save_current()

    storage = db.load_fin_item(iid)
    assert storage["payload"]["seed_phrase"] == _SEED12
    assert storage["payload"]["addresses"] == [
        {"network": "ETH", "address": "0xabc", "label": ""}]


def test_link_from_fin_card_shows_on_account(window):
    """Привязка аккаунта на карточке записи → fin_links в БД; открытая карточка
    аккаунта показывает запись в «ПРИВЯЗАННЫЕ КАРТЫ И КОШЕЛЬКИ»."""
    db = window.db
    sid = db.add_service("S")
    aid = db.add_account(sid, "Acc")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    window._reload_tree()

    window._select_node(CARD, iid)
    window.edit_current()
    window.fin_tabs.f_linked_accounts.set_data([{"id": aid, "name": "Acc"}])
    window.save_current()
    assert db.get_item_links(iid) == [aid]

    window._select_node(ACCOUNT, aid)
    db.wait_executor_idle()
    assert window.tabs.f_fin_linked.get_data() == [iid]


def test_link_from_account_card_shows_on_item(window):
    """Привязка записи на карточке аккаунта (сохранение аккаунта пишет связи
    той же транзакцией) → отображение на карточке записи."""
    db = window.db
    sid = db.add_service("S")
    aid = db.add_account(sid, "Acc")
    iid = db.add_fin_item(sid, "crypto_wallet", "Кошелёк")
    window._reload_tree()

    window._select_node(ACCOUNT, aid)
    db.wait_executor_idle()
    window.edit_current()
    window.tabs.f_fin_linked.set_data(
        [{"id": iid, "item_type": "crypto_wallet", "name": "Кошелёк",
          "card_last4": None}])
    window.save_current()
    db.wait_executor_idle()
    assert db.get_item_links(iid) == [aid]

    window._select_node(WALLET, iid)
    assert window.fin_tabs.f_linked_accounts.get_data() == [aid]


def test_ui_links_survive_bin_restore(window):
    """Связь переживает корзину: после restore карточка аккаунта снова видит
    запись (концепт §8)."""
    db = window.db
    sid = db.add_service("S")
    aid = db.add_account(sid, "Acc")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.set_item_links(iid, [aid])
    db.move_fin_item_to_bin(iid)
    db.restore_fin_item(iid)
    window._reload_tree()

    window._select_node(ACCOUNT, aid)
    db.wait_executor_idle()
    assert window.tabs.f_fin_linked.get_data() == [iid]


def test_fin_link_navigation_from_account(window):
    """Клик по привязанной записи выделяет её узел в дереве."""
    db = window.db
    sid = db.add_service("S")
    aid = db.add_account(sid, "Acc")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.set_item_links(iid, [aid])
    window._reload_tree()

    window.on_fin_link_navigate(CARD, iid)
    node = window._node(window.tree.currentItem())
    assert node["type"] == CARD and node["id"] == iid
