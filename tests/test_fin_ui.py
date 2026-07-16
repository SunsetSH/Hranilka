"""UI Фазы 1 фин-сущностей: FinItemTabs из дескриптора, поля карты
(MaskedCardNumberField/ExpiryField), создание/сохранение карты через MainWindow,
ключ несохранённых правок (CARD, id), восстановление карты из корзины.

Строится настоящий MainWindow offscreen на временной БД (по образцу
test_mainwindow.py). util.fire без работающего qasync-loop выполняет корутины
синхронно, поэтому прямой _select_node загружает карточку до конца.
"""
import pytest

from PySide6.QtWidgets import QApplication

from hranilka.core.nodetypes import CARD
from hranilka.core.fin_types import FIN_TYPES
from hranilka.ui.widgets import MaskedCardNumberField, ExpiryField


@pytest.fixture
def window(qapp, tmp_path, monkeypatch, dispose_window):
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    win = main.MainWindow()
    # Подавляем отложенный показ обучения (QTimer.singleShot в __init__):
    # иначе всплывающий WelcomeDialog утекает между тестами.
    win.config.set("welcome_shown", True)
    win.config.set("show_fin_instruments", True)
    win.apply_config()
    yield win
    dispose_window(win)


# ─── FinItemTabs строится из дескриптора ──────────────────────────────────────

def test_fin_tabs_built_from_descriptor(qapp):
    from hranilka.ui.fin_tabs import FinItemTabs
    spec = FIN_TYPES["bank_card"]
    tabs = FinItemTabs(spec)
    # Все поля дескриптора представлены виджетами; спец-поля — своими виджетами.
    keys = {k for k, _ in tabs.fields()}
    assert keys == {f.key for f in spec.fields}
    assert isinstance(tabs._widgets["card_number"], MaskedCardNumberField)
    assert isinstance(tabs._widgets["expiry"], ExpiryField)


def test_fin_tabs_payload_roundtrip(qapp):
    from hranilka.ui.fin_tabs import FinItemTabs
    tabs = FinItemTabs(FIN_TYPES["bank_card"])
    tabs.set_all_editable(True)
    payload = {
        "card_number": "4111111111111111", "expiry": "12/25", "cvv": "123",
        "pin": "0000", "cardholder": "IVAN IVANOV", "holder_address": "Москва",
        "payment_system": "Visa", "bank_name": "Tinkoff", "card_kind": "дебетовая",
        "currency": "RUB", "issue_date": "2024-01-15",
        "account_number": "40817810000000001234", "iban": "RU00", "bik": "044",
        "corr_account": "30101", "inn": "770000", "kpp": "770001",
        "swift": "TICSRU", "credit_limit": "1000", "contract_number": "C-1",
        "bank_phone": "+70000000000", "code_word": "secret",
        "sms_phone": "+79990000000", "notes_bank": "заметка банка",
        "notes": "заметка",
    }
    tabs.load_payload(payload)
    out = tabs.collect_payload()
    for key, value in payload.items():
        assert out[key] == value, key


def test_bank_card_field_placement():
    """У «Базы» нет bank_name; у «Банка» bank_name — первым поле (перенос)."""
    spec = FIN_TYPES["bank_card"]
    base = [f.key for f in spec.fields if f.tab == "База"]
    bank = [f.key for f in spec.fields if f.tab == "Банк"]
    assert "bank_name" not in base
    assert bank[0] == "bank_name"
    # Новые реквизиты счёта присутствуют.
    account = [f.key for f in spec.fields if f.tab == "Счёт"]
    assert {"corr_account", "inn", "kpp"} <= set(account)


def _row_of(page, widget):
    """Индекс строки главного layout вкладки, содержащей widget (в т.ч. внутри
    вложенной строки-группы). None — если не найден."""
    from PySide6.QtWidgets import QHBoxLayout
    main = page.layout()
    for i in range(main.count()):
        item = main.itemAt(i)
        if item.widget() is widget:
            return i
        sub = item.layout()
        if isinstance(sub, QHBoxLayout):
            for j in range(sub.count()):
                col = sub.itemAt(j).layout()
                if col is None:
                    continue
                for k in range(col.count()):
                    if col.itemAt(k).widget() is widget:
                        return i
    return None


def test_row_group_puts_fields_in_one_row(qapp):
    """payment_system и card_kind (общий row_group) — в одной строке; currency
    (без группы) — в отдельной строке."""
    from hranilka.ui.fin_tabs import FinItemTabs
    tabs = FinItemTabs(FIN_TYPES["bank_card"])
    ps = tabs._widgets["payment_system"]
    kind = tabs._widgets["card_kind"]
    currency = tabs._widgets["currency"]
    page = ps.parentWidget()
    assert _row_of(page, ps) is not None
    assert _row_of(page, ps) == _row_of(page, kind)          # одна строка
    assert _row_of(page, currency) != _row_of(page, ps)      # отдельная строка


def test_links_section_on_first_tab_no_links_tab(qapp):
    """Связи — внизу первой вкладки, отдельной вкладки «Связи» нет."""
    from hranilka.ui.fin_tabs import FinItemTabs
    tabs = FinItemTabs(FIN_TYPES["bank_card"])
    labels = [b.text() for b in tabs.tab_buttons()]
    assert "Связи" not in labels
    assert tabs.f_linked_accounts is not None
    # f_linked_accounts живёт на странице первой вкладки (той же, что f_name).
    assert tabs.f_linked_accounts.parentWidget() is tabs.f_name.parentWidget()


def test_fin_name_edit_ui_to_db_and_node(window, monkeypatch):
    """Правка имени карты в f_name сохраняется в БД и обновляет узел дерева."""
    import hranilka.ui.theme as theme_mod
    monkeypatch.setattr(theme_mod, "themed_input", lambda *a, **k: ("Старое", True))
    window.add_fin_record("bank_card")
    iid = window._current_fin[1]
    db = window.db
    db.wait_executor_idle()

    window.tree.setCurrentItem(None)
    window._select_node(CARD, iid)
    assert window.is_editing                             # edit-on-load
    window.fin_tabs.f_name.set_text("Новое имя")
    window.save_current()

    assert db.load_fin_item(iid)["name"] == "Новое имя"
    item = window._find_leaf_item(CARD, iid)
    assert "Новое имя" in item.text(0)


def test_fin_name_empty_keeps_previous(window, monkeypatch):
    """Пустое/пробельное имя при сохранении не затирает прежнее."""
    import hranilka.ui.theme as theme_mod
    monkeypatch.setattr(theme_mod, "themed_input", lambda *a, **k: ("Имя", True))
    window.add_fin_record("bank_card")
    iid = window._current_fin[1]
    db = window.db
    db.wait_executor_idle()

    window.tree.setCurrentItem(None)
    window._select_node(CARD, iid)
    window.fin_tabs.f_name.set_text("   ")               # только пробелы
    window.save_current()
    assert db.load_fin_item(iid)["name"] == "Имя"        # прежнее имя сохранено


def test_fin_tabs_bin_autodetect(qapp):
    """Ввод номера заполняет платёжную систему (BIN-детект), пока пользователь
    не переопределил её вручную."""
    from hranilka.ui.fin_tabs import FinItemTabs
    tabs = FinItemTabs(FIN_TYPES["bank_card"])
    tabs.set_all_editable(True)
    tabs._widgets["card_number"].set_text("4111111111111111")   # Visa
    assert tabs._widgets["payment_system"].get_text() == "Visa"
    # Ручной override — автодетект больше не перетирает.
    tabs._widgets["payment_system"].set_text("Мир")
    tabs._widgets["card_number"].set_text("5500000000000004")   # Mastercard-BIN
    assert tabs._widgets["payment_system"].get_text() == "Мир"


# ─── MaskedCardNumberField ────────────────────────────────────────────────────

def test_masked_card_luhn_visibility_by_mode(qapp):
    """Индикатор Луна скрыт в просмотре и виден в правке."""
    f = MaskedCardNumberField()
    f.set_editable(False)
    assert f.luhn_label.isHidden()                     # в просмотре скрыт
    f.set_editable(True)
    assert not f.luhn_label.isHidden()                 # в правке виден


def test_masked_card_number_field(qapp):
    f = MaskedCardNumberField()
    f.set_editable(False)
    f.set_text("4111 1111 1111 1111")
    assert f.get_text() == "4111111111111111"          # без пробелов
    assert f.input.text() == "**** **** **** 1111"     # маска в просмотре
    assert f.luhn_label.text() == "OK"                 # валидный номер

    # reveal показывает полный номер с группировкой по 4
    f.reveal_btn.setChecked(True)
    f._refresh_view()
    assert f.input.text() == "4111 1111 1111 1111"

    # копирование — без пробелов, с сигналом
    QApplication.clipboard().clear()
    copied = []
    f.copy_signal.connect(lambda: copied.append(True))
    f.do_copy()
    assert QApplication.clipboard().text() == "4111111111111111"
    assert copied == [True]

    # неверная контрольная сумма — предупреждение
    f.set_text("4111111111111234")
    assert "контр" in f.luhn_label.text()

    # пустое поле — пустой индикатор
    f.set_text("")
    assert f.luhn_label.text() == ""


def test_masked_card_number_edit_grouping(qapp):
    f = MaskedCardNumberField()
    f.set_editable(True)
    f.input.setText("4111111111111111")     # эмуляция ручного ввода
    assert f.input.text() == "4111 1111 1111 1111"   # автогруппировка
    assert f.get_text() == "4111111111111111"


# ─── ExpiryField ──────────────────────────────────────────────────────────────

def test_expiry_field(qapp):
    f = ExpiryField()
    f.set_editable(False)
    f.set_text("12/40")                      # далёкое будущее
    assert f.get_text() == "12/40"
    assert f.warn_label.text() == ""
    assert f.warn_label.isHidden()

    f.set_text("01/20")                      # прошедшая дата
    assert f.warn_label.text() == "[!] истекла"
    assert not f.warn_label.isHidden()

    f.set_text("")
    assert f.get_text() == ""
    assert f.warn_label.text() == ""
    assert f.warn_label.isHidden()


def test_expiry_copy_button_has_no_stretch_before_it(qapp):
    """Кнопка копирования идёт сразу после поля/предупреждения, без пустоты."""
    f = ExpiryField()
    layout = f.layout()
    assert all(layout.itemAt(i).spacerItem() is None for i in range(layout.count()))


# ─── Создание карты через MainWindow + сохранение payload ─────────────────────

def test_create_card_via_mainwindow_saves_payload(window, monkeypatch):
    import hranilka.ui.theme as theme_mod
    monkeypatch.setattr(theme_mod, "themed_input", lambda *a, **k: ("Моя карта", True))

    window.add_fin_record("bank_card")
    # Запись создана в БД, узел выбран (карточка — на fin-странице стека).
    assert window._current_fin is not None and window._current_fin[0] == CARD
    iid = window._current_fin[1]
    db = window.db
    # Вне qasync-loop вложенный util.fire (выбор узла → загрузка карточки)
    # оставляет фоновую операцию БД незавершённой — дожидаемся её (тест-специфика;
    # в приложении qasync-loop доводит загрузку сам).
    db.wait_executor_idle()
    db.cursor.execute("SELECT name, item_type FROM fin_items WHERE id = ?", (iid,))
    row = db.cursor.fetchone()
    assert row["name"] == "Моя карта" and row["item_type"] == "bank_card"

    # Синхронно перезагружаем карточку (edit-on-load → режим правки).
    window.tree.setCurrentItem(None)
    window._select_node(CARD, iid)
    assert window.is_editing
    assert window.right_stack.currentWidget() is window.fin_tabs

    window.fin_tabs._widgets["card_number"].set_text("4111111111111111")
    window.fin_tabs._widgets["cvv"].set_text("123")
    window.save_current()                    # роутинг → fin_save (синхронно)

    storage = db.load_fin_item(iid)
    assert storage["payload"]["card_number"] == "4111111111111111"
    assert storage["payload"]["cvv"] == "123"
    db.cursor.execute("SELECT card_last4 FROM fin_items WHERE id = ?", (iid,))
    assert db.cursor.fetchone()["card_last4"] == "1111"   # экстракт-колонка
    assert not window.is_editing


# ─── Enum-поля в просмотре: не disabled и не реагируют на колёсико ─────────────

def _send_wheel(widget):
    """Отправить событие прокрутки колеса напрямую виджету."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, -120),
                     QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                     Qt.ScrollUpdate, False)
    QApplication.sendEvent(widget, ev)


def test_enum_field_view_not_disabled_ignores_wheel(qapp):
    """_EnumField в просмотре: комбо НЕ disabled (текст не приглушён), значение
    читается, а колёсико его не меняет (в правке — обычное поведение)."""
    from hranilka.ui.fin_tabs import FinItemTabs
    from hranilka.ui.widgets import ReadOnlyAwareComboBox
    tabs = FinItemTabs(FIN_TYPES["bank_card"])
    field = tabs._widgets["currency"]
    combo = field.combo
    assert isinstance(combo, ReadOnlyAwareComboBox)

    tabs.set_all_editable(False)                       # просмотр
    assert combo.isEnabled()                           # не disabled → не приглушён
    field.set_text("USD")
    assert field.get_text() == "USD"
    _send_wheel(combo)
    assert field.get_text() == "USD"                   # колесо не изменило значение


def test_kv_list_enum_view_ignores_wheel(qapp):
    """enum-ячейка KeyValueListWidget в просмотре: не disabled и глуха к колесу."""
    from hranilka.ui.widgets import KeyValueListWidget, ReadOnlyAwareComboBox
    spec = next(ls for ls in FIN_TYPES["crypto_wallet"].lists
                if ls.key == "addresses")
    w = KeyValueListWidget(spec.item_fields)
    w.add_row({"network": "ETH", "address": "0xabc", "label": ""})  # просмотр по умолчанию
    combo = w.rows[0].fields["network"]
    assert isinstance(combo, ReadOnlyAwareComboBox)
    assert combo.isEnabled()
    before = combo.currentText()
    _send_wheel(combo)
    assert combo.currentText() == before


# ─── Заголовки диалогов создания фин-записей (create_title) ───────────────────

def test_create_title_per_fin_type(window, monkeypatch):
    """add_fin_record берёт заголовок диалога из ItemTypeSpec.create_title —
    свой на каждый тип (не общий «Новая запись»)."""
    import hranilka.ui.theme as theme_mod
    titles = []

    def fake_input(config, parent, title, label, *a, **k):
        titles.append(title)
        return ("", False)                             # отмена — без записи в БД

    monkeypatch.setattr(theme_mod, "themed_input", fake_input)
    window.add_fin_record("bank_card")
    window.add_fin_record("crypto_wallet")
    assert titles == ["Создать банковскую карту", "Создать криптокошелёк"]


# ─── ПКМ-меню фин-листа содержит «Экспорт…» ───────────────────────────────────

def test_fin_leaf_context_menu_has_export(window, monkeypatch):
    """Контекст-меню карты/кошелька в дереве содержит пункт «Экспорт…»."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu
    import hranilka.ui.theme as theme_mod
    from hranilka.ui import tree as tree_mod

    monkeypatch.setattr(theme_mod, "themed_input", lambda *a, **k: ("К", True))
    window.add_fin_record("bank_card")
    iid = window._current_fin[1]
    window.db.wait_executor_idle()
    window._select_node(CARD, iid)
    item = window._find_leaf_item(CARD, iid)

    captured = {}

    class RecMenu(QMenu):
        def exec(self, *a, **k):                        # меню не показываем — снимок пунктов
            captured["texts"] = [act.text() for act in self.actions()]
            return None

    monkeypatch.setattr(tree_mod, "QMenu", RecMenu)
    monkeypatch.setattr(window.tree, "itemAt", lambda pos: item)
    window.show_tree_context_menu(QPoint(0, 0))
    assert "Экспорт…" in captured["texts"]


# ─── Ключ несохранённых правок (CARD, id) ─────────────────────────────────────

def test_fin_dirty_key_is_card_id(window):
    db = window.db
    sid = db.add_service("S")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    window._reload_tree()
    window._select_node(CARD, iid)           # синхронная загрузка (просмотр)
    window.edit_current()                    # → fin_toggle_edit
    assert window.is_editing

    window.fin_tabs._widgets["cvv"].set_text("999")
    # Уходим с карточки → правки стэшатся под ключом (CARD, id).
    window.tree.setCurrentItem(None)
    assert (CARD, iid) in window._dirty_ids
    assert (CARD, iid) in window._edit_cache
    assert all(isinstance(k, tuple) and len(k) == 2 for k in window._dirty_ids)
    # Возврат восстанавливает правки из кеша.
    window._select_node(CARD, iid)
    assert window.fin_tabs._widgets["cvv"].get_text() == "999"


# ─── Корзина карты: restore ───────────────────────────────────────────────────

def test_recycle_bin_restores_card(window):
    from hranilka.ui.dialogs.recycle_bin import RecycleBinDialog
    db = window.db
    sid = db.add_service("S")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.move_fin_item_to_bin(iid)
    assert db.get_deleted_count() == 1

    dlg = RecycleBinDialog(window.config, db, window)
    assert dlg._list.count() == 1
    label = dlg._list.item(0).text()
    assert label.startswith("[$] ")         # префикс типа записи (карта)
    dlg._list.setCurrentRow(0)
    dlg._restore()

    assert dlg.changed
    assert db.get_deleted_count() == 0
    db.cursor.execute("SELECT deleted_at FROM fin_items WHERE id = ?", (iid,))
    assert db.cursor.fetchone()["deleted_at"] is None


# ─── H-02: сбой чтения связей при осиротевшей загрузке не стирает links ────
# (симметрично серверам, tests/test_server_ui.py)

def test_fin_orphan_draft_link_read_failure_preserves_links_on_exit_flush(
        window, monkeypatch):
    """get_item_links падает во время осиротевшей orphan-загрузки —
    черновик получает links=None («неизвестно»), а НЕ [] («снять все»).
    Сохранение черновика при выходе (_save_unsaved_before_exit) не должно
    стереть реальные связи фин-записи."""
    db = window.db
    aid = db.add_account(None, "Провайдер")
    iid = db.add_fin_item(None, "bank_card", "Карта")
    db.set_item_links(iid, [aid])
    window._current_fin = None                    # мы уже не на этой карточке
    window._edit_cache.pop((CARD, iid), None)

    def boom(*_a, **_k):
        raise RuntimeError("database is locked")
    with monkeypatch.context() as m:
        m.setattr(db, "get_item_links", boom)
        window._on_fin_gallery_orphan_upload((CARD, iid), "скрин", b"\x89PNGfake")

    assert (CARD, iid) in window._edit_cache
    assert window._edit_cache[(CARD, iid)]["links"] is None
    assert (CARD, iid) in window._dirty_ids

    assert window._save_unsaved_before_exit() is True
    assert db.get_item_links(iid) == [aid]         # связь НЕ стёрта


def test_open_fin_orphan_draft_with_unknown_links_refetches_and_preserves(window):
    """Черновик с links=None (сконструирован напрямую): обычное ОТКРЫТИЕ
    карточки должно перечитать связи из БД, а не показать пустой список.
    Последующее ручное сохранение не стирает исходные fin_links (симметрично
    серверам, tests/test_server_ui.py)."""
    db = window.db
    aid = db.add_account(None, "Провайдер")
    iid = db.add_fin_item(None, "bank_card", "Карта")
    db.set_item_links(iid, [aid])
    window._reload_tree()

    window._edit_cache[(CARD, iid)] = {
        "storage": db.load_fin_item(iid), "links": None}
    window._dirty_ids.add((CARD, iid))

    window._select_node(CARD, iid)

    assert window._edit_cache[(CARD, iid)]["links"] == [aid]
    assert window.fin_tabs.f_linked_accounts.get_data()

    window.save_current()

    assert db.get_item_links(iid) == [aid]         # связь НЕ стёрта


def test_open_fin_orphan_draft_with_unknown_links_fails_closed_on_second_error(
        window, monkeypatch):
    """Если и повторное чтение связей при открытии черновика падает —
    карточка НЕ открывается (fail-closed), а не молча показывает []."""
    import hranilka.ui.theme as theme_mod
    db = window.db
    aid = db.add_account(None, "Провайдер")
    iid = db.add_fin_item(None, "bank_card", "Карта")
    db.set_item_links(iid, [aid])
    window._reload_tree()

    window._edit_cache[(CARD, iid)] = {
        "storage": db.load_fin_item(iid), "links": None}
    window._dirty_ids.add((CARD, iid))

    def boom(*_a, **_k):
        raise RuntimeError("database is locked")

    with monkeypatch.context() as m:
        # _show_card_error зовёт модальный themed_info (d.exec) — гасим.
        m.setattr(theme_mod, "themed_info", lambda *a, **k: None)
        m.setattr(db, "get_item_links", boom)
        window._select_node(CARD, iid)

    assert window._current_fin is None              # карточка не открыта
    assert window._edit_cache[(CARD, iid)]["links"] is None  # черновик цел
    assert db.get_item_links(iid) == [aid]          # связь в БД не тронута


# ─── M-02: выключение show_fin_instruments гасит незавершённые операции ───
# (симметрично серверам, tests/test_server_ui.py — управляемый delayed-future,
# паттерн tests/test_review_07_gallery.py)

async def test_hide_fin_invalidates_pending_load(window, monkeypatch):
    import asyncio
    db = window.db
    iid = db.add_fin_item(None, "bank_card", "Карта")
    window._reload_tree()

    gate = asyncio.Event()
    orig_run_async = db.run_async

    async def gated_run_async(method, *args, **kwargs):
        if getattr(method, "__name__", "") == "load_fin_item":
            await gate.wait()
        return await orig_run_async(method, *args, **kwargs)

    monkeypatch.setattr(db, "run_async", gated_run_async)

    window._select_node(CARD, iid)
    await asyncio.sleep(0.01)                  # дать задаче дойти до gate.wait()
    assert window._card_busy is True
    gen_before = window._card_gen

    window.config.set("show_fin_instruments", False)
    window.apply_config()                      # _hide_fin_everywhere: gen++, busy сброшен

    assert window._card_gen != gen_before
    assert window._card_busy is False
    assert window._current_fin is None
    assert window.right_stack.currentWidget() is window.placeholder_label

    gate.set()
    await asyncio.sleep(0.01)
    db.wait_executor_idle()
    await asyncio.sleep(0.05)

    assert window._current_fin is None
    assert window.right_stack.currentWidget() is window.placeholder_label
    assert window.is_editing is False


async def test_hide_fin_cancels_pending_gallery_upload(window, monkeypatch):
    import asyncio
    db = window.db
    iid = db.add_fin_item(None, "bank_card", "Карта")
    window._reload_tree()
    window._select_node(CARD, iid)
    window.edit_current()

    gw = window.fin_tabs.f_gallery_widget
    gate = asyncio.Event()                     # никогда не выставляем — задача висит
    orphans = []

    async def _gated_pipeline(item, path, gen, account_id=None, limit_context=None):
        await gate.wait()
        orphans.append(account_id)             # сюда дойти не должны

    monkeypatch.setattr(gw, "_upload_pipeline", _gated_pipeline)
    gw._queue_file_load("x.png")
    assert gw.has_pending_uploads() is True

    window.config.set("show_fin_instruments", False)
    window.apply_config()                      # M-02: cancel_all_tasks фин-галерей

    assert gw.has_pending_uploads() is False
    await gw.wait_pending_uploads()
    assert orphans == []
    assert not any(k[0] == CARD for k in window._edit_cache)
