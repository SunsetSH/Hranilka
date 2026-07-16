"""UI этапа 4 VPS-серверов: карточка (ServerTabs/ServerCardMixin), создание,
редактирование/сохранение/отмена, stash несохранённых правок, двусторонняя
связь server<->account с навигацией, видимость секции «ПРИВЯЗАННЫЕ СЕРВЕРЫ»
по флагу show_servers, галерея-смоук.

Строится настоящий MainWindow offscreen на временной БД (по образцу
test_fin_ui.py). util.fire без работающего qasync-loop выполняет корутины
синхронно, поэтому прямой _select_node загружает карточку до конца.
"""
import pytest

from hranilka.core.nodetypes import SERVER, ACCOUNT


@pytest.fixture
def window(qapp, tmp_path, monkeypatch, dispose_window):
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    win = main.MainWindow()
    win.config.set("welcome_shown", True)
    win.config.set("show_servers", True)
    win.apply_config()
    yield win
    dispose_window(win)


# ─── Открытие карточки из дерева ───────────────────────────────────────────

def test_open_server_card_from_tree(window):
    db = window.db
    sid = db.add_server(None, "Мой VPS")
    window._reload_tree()
    window._select_node(SERVER, sid)

    assert window._current_server == (SERVER, sid)
    assert window.right_stack.currentWidget() is window.server_tabs
    assert window.server_tabs.f_name.get_text() == "Мой VPS"
    assert not window.is_editing


# ─── Создание кнопкой «+ СЕРВЕР» ───────────────────────────────────────────

def test_create_server_via_button(window, monkeypatch):
    import hranilka.ui.theme as theme_mod
    monkeypatch.setattr(theme_mod, "themed_input", lambda *a, **k: ("Новый сервер", True))

    assert not window.srv_buttons_widget.isHidden()
    window.add_server()

    assert window._current_server is not None
    sid = window._current_server[1]
    db = window.db
    # Вне qasync-loop вложенный util.fire (выбор узла → загрузка карточки)
    # оставляет фоновую операцию БД незавершённой — дожидаемся её (тест-специфика;
    # в приложении qasync-loop доводит загрузку сам, см. test_fin_ui.py).
    db.wait_executor_idle()
    db.cursor.execute("SELECT name FROM servers WHERE id = ?", (sid,))
    assert db.cursor.fetchone()["name"] == "Новый сервер"

    # Синхронно перезагружаем карточку (edit-on-load → режим правки).
    window.tree.setCurrentItem(None)
    window._select_node(SERVER, sid)
    assert window.is_editing
    assert window.right_stack.currentWidget() is window.server_tabs


def test_add_server_button_hidden_when_flag_off(window):
    window.config.set("show_servers", False)
    window.apply_config()
    assert window.srv_buttons_widget.isHidden()
    window.config.set("show_servers", True)
    window.apply_config()
    assert not window.srv_buttons_widget.isHidden()


# ─── Редактирование + сохранение (payload и имя в БД) ──────────────────────

def test_edit_and_save_server_payload(window):
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()
    assert window.is_editing

    window.server_tabs.f_name.set_text("Переименованный")
    window.server_tabs._widgets["hosting"].set_text("Aeza / промо")
    window.server_tabs._widgets["host"].set_text("203.0.113.10")
    window.server_tabs._widgets["ssh_port"].set_text("2222")
    window.server_tabs._widgets["paid_until"].set_text("2030-01-01")
    dict(window.server_tabs.list_fields())["os_users"].set_items(
        [{"login": "root", "password": "secret", "role": "root", "label": ""}])

    window.save_current()

    storage = db.get_server(sid)
    assert storage["name"] == "Переименованный"
    assert storage["payload"]["hosting"] == "Aeza / промо"
    assert storage["payload"]["host"] == "203.0.113.10"
    assert storage["payload"]["ssh_port"] == "2222"
    assert storage["payload"]["paid_until"] == "2030-01-01"
    assert storage["payload"]["os_users"][0]["login"] == "root"
    db.cursor.execute("SELECT paid_until FROM servers WHERE id = ?", (sid,))
    assert db.cursor.fetchone()["paid_until"] == "2030-01-01"   # экстракт-колонка
    assert not window.is_editing

    item = window._find_leaf_item(SERVER, sid)
    assert "Переименованный" in item.text(0)


def test_ssh_keys_and_panels_roundtrip(window):
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    ssh = dict(window.server_tabs.list_fields())["ssh_keys"]
    ssh.set_items([{"label": "осн", "key_type": "ed25519",
                    "public_key": "ssh-ed25519 AAAA", "private_key": "-----BEGIN",
                    "passphrase": "pp"}])
    panels = dict(window.server_tabs.list_fields())["panels"]
    panels.set_items([{"panel_type": "3x-ui", "url": "https://1.2.3.4",
                       "login": "admin", "password": "pw"}])
    window.save_current()

    storage = db.get_server(sid)
    assert storage["payload"]["ssh_keys"][0]["private_key"] == "-----BEGIN"
    assert storage["payload"]["panels"][0] == {
        "panel_type": "3x-ui", "url": "https://1.2.3.4",
        "login": "admin", "password": "pw"}
    assert "port" not in storage["payload"]["panels"][0]
    assert "label" not in storage["payload"]["panels"][0]


def test_panels_row_is_two_rows_with_narrow_type_field(window):
    """Панели: ряд 1 «Тип панели»(узкое)+«URL», ряд 2 «Логин»/«Пароль», ряд 3
    кнопки генерации пароля (УИ §2026-07-16) — структурная проверка
    раскладки строки списка."""
    panels = dict(window.server_tabs.list_fields())["panels"]
    panels.add_row({"panel_type": "3x-ui", "url": "https://x",
                    "login": "admin", "password": "pw"})
    row = panels.rows[0]
    outer = row.widget.layout()
    assert outer.count() == 3                     # ряд1 + ряд2 + ряд кнопок генерации

    row1 = outer.itemAt(0).layout()
    assert row1.itemAt(0).widget() is row.fields["panel_type"]
    assert row1.itemAt(1).widget() is row.fields["url"]
    assert row1.itemAt(2).widget() is row.copy_btn      # [КОП]/[X] — конец ряда 1
    assert row1.itemAt(3).widget() is row.del_btn

    # Ряд 2: логин + своя [КОП], пароль + reveal + своя [КОП] (FieldSpec.
    # copy_btn — до этого ряда общая [КОП] строки, конец ряда 1, не
    # достаёт). Кнопки генерации (gen_btn) больше не инлайн у поля — они в
    # отдельном ряду 3, под всеми полями строки (УИ §2026-07-16).
    row2 = outer.itemAt(1).layout()
    assert row2.count() == 5
    assert row2.itemAt(0).widget() is row.fields["login"]
    assert row2.itemAt(1).widget() is row.field_copy_btns["login"]
    assert row2.itemAt(2).widget() is row.fields["password"]
    assert row2.itemAt(4).widget() is row.field_copy_btns["password"]

    # Ряд 3: «СГЕНЕРИРОВАТЬ ПАРОЛЬ» + «ПАРАМЕТРЫ ГЕНЕРАЦИИ», равной ширины
    # (как в карточке аккаунта, ui/tabs.py: create_tab_login).
    row3 = outer.itemAt(2).layout()
    assert row3.count() == 2
    assert row3.itemAt(0).widget() is row.field_gen_btns["password"]
    assert row3.itemAt(1).widget() is row.field_gen_settings_btns["password"]
    assert row.field_gen_btns["password"].minimumWidth() == \
        row.field_gen_settings_btns["password"].minimumWidth()

    type_field = row.fields["panel_type"]
    expected_width = type_field.fontMetrics().horizontalAdvance("Тип панели") + 40
    assert type_field.maximumWidth() == expected_width


# ─── Генератор пароля в «Пользователи»/«Панели» (УИ §2026-07-16) ───────────
#
# Тот же password_gen/GeneratorSettingsDialog, что у аккаунта, и ЕДИНЫЙ
# config-ключ (password_gen.CONFIG_KEY) — никаких отдельных настроек для
# серверов. Раскладка — как у аккаунта (ui/tabs.py: create_tab_login): под
# полями КАЖДОГО экземпляра строки — ряд из двух равных по ширине кнопок
# «СГЕНЕРИРОВАТЬ ПАРОЛЬ»/«ПАРАМЕТРЫ ГЕНЕРАЦИИ» (видимы только в правке).
# kv_list получает callbacks от ServerTabs (gen_callback/gen_settings_
# callback), сам про генератор не знает (см. ui/widgets/kv_list.py).

def test_os_user_gen_btn_uses_shared_generator_settings(window):
    """«СГЕНЕРИРОВАТЬ ПАРОЛЬ» строки «Пользователи» читает настройки из ТОГО
    ЖЕ config-ключа, что и генератор аккаунта — не отдельные настройки для
    серверов."""
    from hranilka.generators import password_gen
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    window.config.set(password_gen.CONFIG_KEY, {
        "mode": "password", "pw_length": 40, "pw_upper": True, "pw_lower": True,
        "pw_digits": True, "pw_symbols": False, "pw_min_digits": 0,
        "pw_min_symbols": 0, "pw_exclude_similar": False,
        "ph_words": 5, "ph_separator": "-", "ph_capitalize": True, "ph_digit": True,
    })

    users = dict(window.server_tabs.list_fields())["os_users"]
    users.set_items([{"login": "root", "password": "", "role": "root", "label": ""}])
    row = users.rows[0]
    assert "password" in row.field_gen_btns
    row.field_gen_btns["password"].click()
    assert len(row.fields["password"].text()) == 40


def test_os_users_row_is_two_rows_with_login_password_copy_buttons(window):
    """Пользователи ОС: ряд 1 «Логин»+«Пароль» (оба со своей [КОП]), ряд 2
    «Роль»+«Метка», ряд 3 — кнопки генерации пароля. Общая [КОП] строки
    оказалась бы в одном ряду с полем-«значением» (пароль, у него уже своя
    [КОП]) — буквальный визуальный дубль, поэтому подавлена (row.copy_btn —
    None); del_btn остаётся."""
    users = dict(window.server_tabs.list_fields())["os_users"]
    users.add_row({"login": "root", "password": "pw", "role": "root", "label": "осн"})
    row = users.rows[0]
    outer = row.widget.layout()
    assert outer.count() == 3                     # ряд1 + ряд2 + ряд кнопок генерации

    row1 = outer.itemAt(0).layout()
    assert row1.count() == 6                      # login+[КОП], password+reveal+[КОП], [X]
    assert row1.itemAt(0).widget() is row.fields["login"]
    assert row1.itemAt(1).widget() is row.field_copy_btns["login"]
    assert row1.itemAt(2).widget() is row.fields["password"]
    assert row1.itemAt(4).widget() is row.field_copy_btns["password"]
    assert row.copy_btn is None                   # подавлена — дублировала бы
    assert row.del_btn is not None
    assert row1.itemAt(row1.count() - 1).widget() is row.del_btn

    row2 = outer.itemAt(1).layout()
    assert row2.count() == 2                      # роль + метка, без [КОП]/[X]
    assert row2.itemAt(0).widget() is row.fields["role"]
    assert row2.itemAt(1).widget() is row.fields["label"]


def test_os_user_login_password_field_copy_buttons_copy_values(window):
    from PySide6.QtWidgets import QApplication
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    users = dict(window.server_tabs.list_fields())["os_users"]
    users.set_items([{"login": "root", "password": "secr3t",
                      "role": "root", "label": ""}])
    users.set_editable(False)
    row = users.rows[0]

    QApplication.clipboard().clear()
    copied = []
    users.copy_signal.connect(lambda: copied.append(True))
    row.field_copy_btns["login"].click()
    assert QApplication.clipboard().text() == "root"

    row.field_copy_btns["password"].click()
    assert QApplication.clipboard().text() == "secr3t"
    assert copied == [True, True]


def test_panel_login_password_have_field_copy_buttons(window):
    """Логин/пароль панелей — во втором row_group-ряду, до которого общая
    [КОП] строки (крепится к первому ряду) не достаёт — своя [КОП] у обоих
    полей (FieldSpec.copy_btn), тот же канал автоочистки буфера."""
    from PySide6.QtWidgets import QApplication
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    panels = dict(window.server_tabs.list_fields())["panels"]
    panels.set_items([{"panel_type": "3x-ui", "url": "https://x",
                       "login": "admin", "password": "pw"}])
    panels.set_editable(False)
    row = panels.rows[0]

    QApplication.clipboard().clear()
    copied = []
    panels.copy_signal.connect(lambda: copied.append(True))
    row.field_copy_btns["login"].click()
    assert QApplication.clipboard().text() == "admin"

    row.field_copy_btns["password"].click()
    assert QApplication.clipboard().text() == "pw"
    assert copied == [True, True]


def test_panel_gen_btn_fills_password_field(window):
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    panels = dict(window.server_tabs.list_fields())["panels"]
    panels.set_items([{"panel_type": "3x-ui", "url": "https://x",
                       "login": "admin", "password": ""}])
    row = panels.rows[0]
    row.field_gen_btns["password"].click()
    assert row.fields["password"].text() != ""


def test_gen_buttons_present_per_row_on_applicable_tabs(window):
    """«СГЕНЕРИРОВАТЬ ПАРОЛЬ»/«ПАРАМЕТРЫ ГЕНЕРАЦИИ» рендерятся под полями
    КАЖДОГО экземпляра строки «Пользователи»/«Панели» (SSH-ключи без поля
    пароля — кнопок нет), а не одна пара на вкладку."""
    from PySide6.QtWidgets import QPushButton
    users = dict(window.server_tabs.list_fields())["os_users"]
    panels = dict(window.server_tabs.list_fields())["panels"]
    ssh_keys = dict(window.server_tabs.list_fields())["ssh_keys"]
    users.set_items([{"login": "root", "password": "", "role": "root", "label": ""},
                     {"login": "deploy", "password": "", "role": "user", "label": ""}])
    panels.set_items([{"panel_type": "3x-ui", "url": "https://x",
                       "login": "admin", "password": ""}])
    ssh_keys.set_items([{"label": "осн", "key_type": "ed25519",
                         "public_key": "", "private_key": "", "passphrase": ""}])

    def count(text):
        return len([b for b in window.server_tabs.findChildren(QPushButton)
                    if b.text() == text])

    assert count("СГЕНЕРИРОВАТЬ ПАРОЛЬ") == 3   # 2 строки users + 1 строка panels
    assert count("ПАРАМЕТРЫ ГЕНЕРАЦИИ") == 3
    assert users.rows[0].field_gen_settings_btns["password"] is not \
        users.rows[1].field_gen_settings_btns["password"]


def test_gen_buttons_visible_only_in_edit_mode(window):
    """Кнопки генерации видимы только в правке — как gen_pass_btn/gen_pass_cfg_btn
    у аккаунта (ui/tabs.py: set_all_editable)."""
    users = dict(window.server_tabs.list_fields())["os_users"]
    users.set_items([{"login": "root", "password": "", "role": "root", "label": ""}])
    row = users.rows[0]

    users.set_editable(False)
    assert row.field_gen_btns["password"].isHidden()
    assert row.field_gen_settings_btns["password"].isHidden()

    users.set_editable(True)
    assert not row.field_gen_btns["password"].isHidden()
    assert not row.field_gen_settings_btns["password"].isHidden()


def test_gen_settings_button_opens_dialog_with_shared_config(window, monkeypatch):
    """Кнопка открывает ТОТ ЖЕ диалог, что у аккаунта, с общим config —
    никакого отдельного объекта настроек для серверов."""
    from PySide6.QtWidgets import QDialog
    from hranilka.ui.generator_dialog import GeneratorSettingsDialog

    opened = []

    def fake_exec(self):
        opened.append(self.config is window.config)
        return QDialog.Rejected

    monkeypatch.setattr(GeneratorSettingsDialog, "exec", fake_exec)
    users = dict(window.server_tabs.list_fields())["os_users"]
    users.set_items([{"login": "root", "password": "", "role": "root", "label": ""}])
    row = users.rows[0]
    row.field_gen_settings_btns["password"].click()
    assert opened == [True]


def test_gen_settings_accept_saves_shared_config_key(window, monkeypatch):
    """Callback ServerTabs._open_gen_settings_for_row сохраняет ЕДИНЫЙ
    config-ключ генератора (как AccountCardMixin.open_password_generator_
    settings) — та же логика, что дёргает кнопка «ПАРАМЕТРЫ ГЕНЕРАЦИИ»."""
    from hranilka.generators import password_gen
    saved = []
    monkeypatch.setattr(window.config, "save", lambda: saved.append(True))

    from PySide6.QtWidgets import QDialog

    def fake_exec(self):
        self.config.set(password_gen.CONFIG_KEY, {"mode": "phrase"})
        return QDialog.Accepted

    from hranilka.ui.generator_dialog import GeneratorSettingsDialog
    monkeypatch.setattr(GeneratorSettingsDialog, "exec", fake_exec)

    result = window.server_tabs._open_gen_settings_for_row()

    assert window.config.get(password_gen.CONFIG_KEY) == {"mode": "phrase"}
    assert saved == [True]
    assert result is not None                       # пароль предпросмотра


def test_gen_settings_reject_returns_none_and_does_not_save(window, monkeypatch):
    """Отмена диалога («Отмена»/закрытие) — конфиг НЕ сохраняется, callback
    возвращает None (kv_list поле не трогает)."""
    saved = []
    monkeypatch.setattr(window.config, "save", lambda: saved.append(True))

    from PySide6.QtWidgets import QDialog
    from hranilka.ui.generator_dialog import GeneratorSettingsDialog
    monkeypatch.setattr(GeneratorSettingsDialog, "exec", lambda self: QDialog.Rejected)

    result = window.server_tabs._open_gen_settings_for_row()

    assert result is None
    assert saved == []


def test_gen_settings_dialog_accept_substitutes_password_into_row_field(
        window, monkeypatch):
    """Идентично аккаунту (AccountCardMixin.open_password_generator_settings):
    «ПАРАМЕТРЫ ГЕНЕРАЦИИ» → диалог → сохранение → новый пароль из
    предпросмотра подставлен в поле пароля ИМЕННО этого экземпляра строки, а
    не в какое-то общее поле."""
    from PySide6.QtWidgets import QDialog
    from hranilka.ui.generator_dialog import GeneratorSettingsDialog

    def fake_exec(self):
        return QDialog.Accepted

    monkeypatch.setattr(GeneratorSettingsDialog, "exec", fake_exec)
    monkeypatch.setattr(GeneratorSettingsDialog, "preview_text",
                        lambda self: "NewGeneratedPass!1")

    users = dict(window.server_tabs.list_fields())["os_users"]
    users.set_items([
        {"login": "root", "password": "old", "role": "root", "label": ""},
        {"login": "deploy", "password": "other-old", "role": "user", "label": ""},
    ])
    row0, row1 = users.rows

    row0.field_gen_settings_btns["password"].click()

    assert row0.fields["password"].text() == "NewGeneratedPass!1"
    assert row1.fields["password"].text() == "other-old"    # соседняя строка не тронута


# ─── «База»: Доп. IP списком, «Стоимость» (УИ §2026-07-15) ─────────────────

def test_extra_ips_and_price_roundtrip(window):
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    window.server_tabs._widgets["price"].set_text("5 USD/мес")
    ips = window.server_tabs._extra_ips_widget
    ips.set_items([{"ip": "203.0.113.11"}, {"ip": "2a01:4f8::1"}])
    window.save_current()

    storage = db.get_server(sid)
    assert storage["payload"]["price"] == "5 USD/мес"
    assert storage["payload"]["extra_ips"] == ["203.0.113.11", "2a01:4f8::1"]

    # Перезагрузка карточки восстанавливает список из сохранённого payload.
    window._select_node(SERVER, sid)
    assert [d["ip"] for d in window.server_tabs._extra_ips_widget.get_items()] == [
        "203.0.113.11", "2a01:4f8::1"]
    assert window.server_tabs._widgets["price"].get_text() == "5 USD/мес"


def test_extra_ips_tolerant_legacy_string_shown_in_card(window):
    """Старый payload с extra_ips одной строкой открывается в карточке как
    список (толерантность на уровне ServerData, УИ §2026-07-15)."""
    db = window.db
    sid = db.add_server(None, "Сервер")
    storage = db.get_server(sid)
    storage["payload"] = {"v": 1, "extra_ips": "10.0.0.5\n10.0.0.6"}
    db.save_server(sid, storage)
    window._reload_tree()
    window._select_node(SERVER, sid)

    assert [d["ip"] for d in window.server_tabs._extra_ips_widget.get_items()] == [
        "10.0.0.5", "10.0.0.6"]


def test_extra_ips_copy_signal_wired_to_clipboard_autoclear(window):
    """«Доп. IP» — список вне server_tabs.list_fields() (список строк, не
    словарей), но [КОП] обязан идти через тот же канал автоочистки буфера,
    что и остальные копируемые поля карточки (main_window.py)."""
    window.config.set("clipboard_clear_secs", 5)
    ips = window.server_tabs._extra_ips_widget
    ips.set_items([{"ip": "203.0.113.11"}])
    ips._copy_row(ips.rows[0])
    assert window._clip_timer.isActive()


# ─── Отмена правок ──────────────────────────────────────────────────────────

def test_cancel_server_edit_discards_changes(window):
    db = window.db
    sid = db.add_server(None, "Исходное имя")
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()
    window.server_tabs.f_name.set_text("Испорченное имя")

    window.cancel_current()

    assert not window.is_editing
    assert window.server_tabs.f_name.get_text() == "Исходное имя"
    assert db.get_server(sid)["name"] == "Исходное имя"


# ─── Stash несохранённых правок при переключении узла ──────────────────────

def test_server_stash_on_switch_and_dirty_marker(window):
    db = window.db
    sid = db.add_server(None, "Сервер")
    aid = db.add_account(None, "Другой аккаунт")
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()
    window.server_tabs._widgets["hosting"].set_text("Черновик хостинга")

    # Уходим на другой узел -> правки стэшатся под ключом (SERVER, id).
    window._select_node(ACCOUNT, aid)
    assert (SERVER, sid) in window._dirty_ids
    assert (SERVER, sid) in window._edit_cache

    item = window._find_leaf_item(SERVER, sid)
    assert "НЕ СОХРАНЕНО" in item.text(0)

    # Возврат восстанавливает правки из кеша.
    window._select_node(SERVER, sid)
    assert window.is_editing
    assert window.server_tabs._widgets["hosting"].get_text() == "Черновик хостинга"


# ─── Связь server <-> account с обеих сторон + навигация ──────────────────

def test_server_account_link_both_sides_and_navigation(window):
    db = window.db
    sid = db.add_server(None, "Сервер")
    aid = db.add_account(None, "Провайдер-аккаунт")

    db.set_server_links(sid, [aid])
    window._reload_tree()

    # Сторона сервера видит аккаунт.
    window._select_node(SERVER, sid)
    links = window.server_tabs.f_linked_accounts.get_data()
    assert links == [aid]

    # Сторона аккаунта видит сервер.
    window._select_node(ACCOUNT, aid)
    server_links = window.tabs.f_server_linked.get_data()
    assert server_links == [sid]

    # Навигация с карточки аккаунта к серверу.
    window.on_server_link_navigate(sid)
    node = window._node(window.tree.currentItem())
    assert node["type"] == SERVER and node["id"] == sid

    # Навигация с карточки сервера к аккаунту.
    window._select_node(SERVER, sid)
    window.on_server_account_link_navigate(aid)
    node = window._node(window.tree.currentItem())
    assert node["type"] == ACCOUNT and node["id"] == aid


def test_save_account_persists_server_links(window):
    """Сохранение карточки аккаунта пишет server_links атомарно вместе с
    карточкой (save_account_with_links, server_ids)."""
    db = window.db
    sid = db.add_server(None, "Сервер")
    aid = db.add_account(None, "Аккаунт")
    window._reload_tree()
    window._select_node(ACCOUNT, aid)
    window.edit_current()
    window.tabs.f_server_linked.set_data([{"id": sid, "name": "Сервер"}])
    window.save_current()

    assert db.get_account_server_links(aid)[0]["id"] == sid
    assert db.get_server_link_accounts(sid)[0]["id"] == aid


# ─── Видимость секции «ПРИВЯЗАННЫЕ СЕРВЕРЫ» по флагу ───────────────────────

def test_server_section_visibility_by_flag(window):
    window.config.set("show_servers", True)
    window.apply_config()
    assert not window.tabs.f_server_linked.isHidden()
    assert not window.tabs.f_server_linked_heading.isHidden()

    window.config.set("show_servers", False)
    window.apply_config()
    assert window.tabs.f_server_linked.isHidden()
    assert window.tabs.f_server_linked_heading.isHidden()


# ─── Темизация карточки сервера (этап 6a) ──────────────────────────────────

def test_server_tabs_scroll_bg_matches_account_tabs(window):
    """apply_appearance должен темизировать server_tabs так же, как account
    tabs: фон QScrollArea/viewport вкладок = main_bg настроек, иначе за
    подписями-QLabel (прозрачный фон) остаётся неокрашенный фон viewport'а
    (баг: тёмные полосы за подписями в светлой теме)."""
    window.config.set("main_bg_color", "#ABCDEF")
    window.apply_config()

    assert window.server_tabs._scroll_areas, "нет вкладок с прокруткой"
    expected = window.tabs._scroll_areas[0].styleSheet()
    assert expected, "эталон (account tabs) сам не темизирован"
    for sa in window.server_tabs._scroll_areas:
        assert sa.styleSheet() == expected
        assert "#ABCDEF" in sa.viewport().styleSheet()


# ─── Галерея — смоук ────────────────────────────────────────────────────────

def test_server_gallery_smoke(window):
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    window.server_tabs.f_gallery_widget.add_item(b"\x89PNGfakebytes", "скрин")
    window.save_current()

    storage = db.get_server(sid)
    assert len(storage["gallery"]) == 1
    assert storage["gallery"][0]["desc"] == "скрин"


# ─── Уведомление об оплате — статус-бар, не баннер (доп. требование 2026-07-15) ──
#
# Баннер-слот правой панели (fin_banner) для серверов больше не используется —
# уведомление показывается в статус-баре при открытии карточки, по образцу
# AccountCardMixin._warn_password_due («ПОРА СМЕНИТЬ ПАРОЛЬ...»).

def test_server_paid_overdue_shows_statusbar_notice(window):
    db = window.db
    sid = db.add_server(None, "Сервер")
    storage = db.get_server(sid)
    storage["payload"] = {"v": 1, "paid_until": "2020-01-01"}
    db.save_server(sid, storage)
    window._reload_tree()

    window._select_node(SERVER, sid)

    assert "ОПЛАТА СЕРВЕРА ПРОСРОЧЕНА" in window.statusBar().currentMessage()
    assert window.fin_banner.isHidden()


def test_server_paid_soon_shows_statusbar_notice(window):
    import datetime as dt
    db = window.db
    sid = db.add_server(None, "Сервер")
    storage = db.get_server(sid)
    soon = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    storage["payload"] = {"v": 1, "paid_until": soon}
    db.save_server(sid, storage)
    window._reload_tree()

    window._select_node(SERVER, sid)

    assert "Оплатить сервер через" in window.statusBar().currentMessage()
    assert window.fin_banner.isHidden()


def test_server_paid_far_future_no_statusbar_notice(window):
    import datetime as dt
    db = window.db
    sid = db.add_server(None, "Сервер")
    storage = db.get_server(sid)
    far = (dt.date.today() + dt.timedelta(days=90)).isoformat()
    storage["payload"] = {"v": 1, "paid_until": far}
    db.save_server(sid, storage)
    window._reload_tree()

    window._select_node(SERVER, sid)

    assert window.statusBar().currentMessage() == ""
    assert window.fin_banner.isHidden()


# ─── H-02: связи orphan-черновика сервера ──────────────────────────────────

def test_server_orphan_draft_reads_links_once(window, monkeypatch):
    """get_server() уже возвращает account_ids в снимке — orphan-путь
    (_server_orphan_into_new_draft) больше не делает отдельного второго
    чтения get_server_links: окно гонки между двумя SELECT исчезает по
    построению, а не только "затыкается" семантикой links=None."""
    db = window.db
    sid = db.add_service("S")
    aid = db.add_account(sid, "Провайдер")
    srv = db.add_server(sid, "VPS")
    db.set_server_links(srv, [aid])
    window._current_server = None                 # мы уже не на этой карточке
    window._edit_cache.pop((SERVER, srv), None)

    real_get_server_links = db.get_server_links
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return real_get_server_links(*a, **k)

    with monkeypatch.context() as m:
        m.setattr(db, "get_server_links", counting)
        window._on_server_gallery_orphan_upload((SERVER, srv), "скрин", b"\x89PNGfake")

    assert calls["n"] == 1                         # только внутри get_server()
    assert window._edit_cache[(SERVER, srv)]["links"] == [aid]
    assert (SERVER, srv) in window._dirty_ids


def test_open_orphan_draft_with_unknown_links_refetches_and_preserves(window):
    """Черновик с links=None (сконструирован напрямую — как после сбоя
    чтения по любой другой причине): обычное ОТКРЫТИЕ карточки должно
    перечитать связи из БД, а не показать пустой список. Последующее ручное
    сохранение не стирает исходные server_links."""
    db = window.db
    sid = db.add_service("S")
    aid = db.add_account(sid, "Провайдер")
    srv = db.add_server(sid, "VPS")
    db.set_server_links(srv, [aid])
    window._reload_tree()

    window._edit_cache[(SERVER, srv)] = {
        "storage": db.get_server(srv), "links": None}
    window._dirty_ids.add((SERVER, srv))

    window._select_node(SERVER, srv)

    # Открытие перечитало связи из БД — черновик больше не «неизвестен».
    assert window._edit_cache[(SERVER, srv)]["links"] == [aid]
    assert window.server_tabs.f_linked_accounts.get_data()

    window.save_current()

    assert db.get_server_links(srv) == [aid]        # связь НЕ стёрта


def test_open_orphan_draft_with_unknown_links_fails_closed_on_second_error(
        window, monkeypatch):
    """Если и повторное чтение связей при открытии черновика падает —
    карточка НЕ открывается (fail-closed), а не молча показывает []."""
    import hranilka.ui.theme as theme_mod
    db = window.db
    sid = db.add_service("S")
    aid = db.add_account(sid, "Провайдер")
    srv = db.add_server(sid, "VPS")
    db.set_server_links(srv, [aid])
    window._reload_tree()

    window._edit_cache[(SERVER, srv)] = {
        "storage": db.get_server(srv), "links": None}
    window._dirty_ids.add((SERVER, srv))

    def boom(*_a, **_k):
        raise RuntimeError("database is locked")

    with monkeypatch.context() as m:
        # _show_card_error зовёт модальный themed_info (d.exec) — гасим.
        m.setattr(theme_mod, "themed_info", lambda *a, **k: None)
        m.setattr(db, "get_server_links", boom)
        window._select_node(SERVER, srv)

    assert window._current_server is None           # карточка не открыта
    assert window._edit_cache[(SERVER, srv)]["links"] is None  # черновик цел
    assert db.get_server_links(srv) == [aid]         # связь в БД не тронута


# ─── M-01: удаление сбрасывает _current_server/current_server_data ─────────

def test_delete_open_server_resets_current_server(window):
    from hranilka.core import util as util_mod
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()
    window._select_node(SERVER, sid)
    assert window._current_server == (SERVER, sid)

    node = window._node(window._find_leaf_item(SERVER, sid))
    util_mod.fire(window._delete_items_async([node], False, True))

    assert window._current_server is None
    assert window.current_server_data is None


def test_delete_container_with_open_server_resets_current_server(window):
    from hranilka.core import util as util_mod
    from hranilka.core.nodetypes import SERVICE
    db = window.db
    svc = db.add_service("Сервис")
    sid = db.add_server(svc, "Сервер")
    window._reload_tree()
    window._select_node(SERVER, sid)
    assert window._current_server == (SERVER, sid)

    svc_item = next(it for it in window._iter_items()
                    if window._node(it)["type"] == SERVICE
                    and window._node(it)["id"] == svc)
    node = window._node(svc_item)
    util_mod.fire(window._delete_items_async([node], False, True))

    assert window._current_server is None
    assert window.current_server_data is None


# ─── M-02: выключение show_servers гасит незавершённые операции ────────────
#
# Управляемый delayed-future (паттерн — tests/test_review_07_gallery.py):
# держим фоновую операцию «в полёте» через asyncio.Event/monkeypatch, гасим
# тумблером, затем отпускаем — результат не должен применяться к UI.

async def test_hide_servers_invalidates_pending_load(window, monkeypatch):
    import asyncio
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()

    gate = asyncio.Event()                     # никогда не выставляем сами
    orig_run_async = db.run_async

    async def gated_run_async(method, *args, **kwargs):
        if getattr(method, "__name__", "") == "get_server":
            await gate.wait()
        return await orig_run_async(method, *args, **kwargs)

    monkeypatch.setattr(db, "run_async", gated_run_async)

    window._select_node(SERVER, sid)
    await asyncio.sleep(0.01)                  # дать задаче дойти до gate.wait()
    assert window._card_busy is True
    gen_before = window._card_gen

    window.config.set("show_servers", False)
    window.apply_config()                      # _hide_servers_everywhere: gen++, busy сброшен

    assert window._card_gen != gen_before
    assert window._card_busy is False
    assert window._current_server is None
    assert window.right_stack.currentWidget() is window.placeholder_label

    gate.set()
    await asyncio.sleep(0.01)                  # дать задаче пройти gate.wait() и уйти в executor
    db.wait_executor_idle()                    # дождаться реального чтения get_server
    await asyncio.sleep(0.05)                  # дать корутине дозавершиться (gen-check)

    # Устаревший результат (сервер существует, чтение бы удалось) НЕ применился.
    assert window._current_server is None
    assert window.right_stack.currentWidget() is window.placeholder_label
    assert window.is_editing is False


async def test_hide_servers_cancels_pending_gallery_upload(window, monkeypatch):
    import asyncio
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    gw = window.server_tabs.f_gallery_widget
    gate = asyncio.Event()                     # никогда не выставляем — задача висит
    orphans = []

    async def _gated_pipeline(item, path, gen, account_id=None, limit_context=None):
        await gate.wait()
        orphans.append(account_id)             # сюда дойти не должны

    monkeypatch.setattr(gw, "_upload_pipeline", _gated_pipeline)
    gw._queue_file_load("x.png")
    assert gw.has_pending_uploads() is True

    window.config.set("show_servers", False)
    window.apply_config()                      # M-02: cancel_all_tasks серверной галереи

    assert gw.has_pending_uploads() is False
    await gw.wait_pending_uploads()
    assert orphans == []                       # задача отменена, конвейер не дошёл до конца
    assert not any(k[0] == SERVER for k in window._edit_cache)


# ─── M-03: повреждённый JSON / нераспознанный paid_until fail-closed ───────

def test_open_corrupted_server_card_fails_closed(window, monkeypatch):
    """Невалидный JSON в servers.data — карточка НЕ открывается (fail-
    closed): заглушка + сообщение об ошибке, исходная строка в БД остаётся
    нетронутой даже после попытки взаимодействия (edit/save на пустом
    текущем состоянии — безопасный no-op)."""
    import hranilka.ui.theme as theme_mod
    db = window.db
    sid = db.add_server(None, "Сервер")
    db.cursor.execute(
        "UPDATE servers SET data = ? WHERE id = ?", ("{broken json", sid))
    db.conn.commit()
    window._reload_tree()

    shown = []
    monkeypatch.setattr(theme_mod, "themed_info",
                        lambda *a, **k: shown.append(a))

    window._select_node(SERVER, sid)

    assert window._current_server is None
    assert window.right_stack.currentWidget() is window.placeholder_label
    assert shown                                # сообщение об ошибке показано

    db.cursor.execute("SELECT data FROM servers WHERE id = ?", (sid,))
    assert db.cursor.fetchone()["data"] == "{broken json"

    # Попытки взаимодействия после fail-closed не трогают БД.
    window.edit_current()
    window.save_current()
    db.cursor.execute("SELECT data FROM servers WHERE id = ?", (sid,))
    assert db.cursor.fetchone()["data"] == "{broken json"


def test_paid_until_unrecognized_value_preserved_on_unrelated_save(window):
    """Старое нераспознанное значение paid_until (не ISO-дата) не пропадает
    молча при сохранении карточки, если пользователь не трогал само поле."""
    db = window.db
    sid = db.add_server(None, "Сервер")
    storage = db.get_server(sid)
    storage["payload"] = {"v": 1, "paid_until": "01.02.2020 (устаревший формат)"}
    db.save_server(sid, storage)
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    # Поле отображается пустым (виджет не умеет показать нераспознанный
    # формат), но значение защищено до явной правки этого же поля.
    assert window.server_tabs._widgets["paid_until"].get_text() == \
        "01.02.2020 (устаревший формат)"
    window.server_tabs._widgets["hosting"].set_text("Новый хостинг")
    window.save_current()

    saved = db.get_server(sid)["payload"]
    assert saved["paid_until"] == "01.02.2020 (устаревший формат)"
    assert saved["hosting"] == "Новый хостинг"


def test_paid_until_cleared_by_explicit_user_edit(window):
    """Если пользователь САМ трогает поле paid_until (даже очищая его),
    "защита" нераспознанного значения снимается — сохраняется то, что
    реально показывает виджет, а не старый мусор."""
    db = window.db
    sid = db.add_server(None, "Сервер")
    storage = db.get_server(sid)
    storage["payload"] = {"v": 1, "paid_until": "мусорное значение"}
    db.save_server(sid, storage)
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    import datetime as dt
    field = window.server_tabs._widgets["paid_until"]
    field._inner.set_date(dt.date(2031, 1, 1))
    field._inner.date_widget.textEdited.emit("01.01.2031")   # реальный пользовательский ввод
    window.save_current()

    assert db.get_server(sid)["payload"]["paid_until"] == "2031-01-01"


# ─── История смены пароля в заметках (docs/ТЗ_VPS_Серверы.md §8.3) ─────────
#
# По образцу аккаунтного механизма (core/domain.append_password_history,
# ui/account_card.py:_append_password_history): при сохранении карточки
# сервера, если пароль пользователя ОС/панели изменился, прежний пароль
# дописывается строкой в заметки — атомарно вместе с сохранением.

_HISTORY_STAMP_RE = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"


def test_server_password_history_records_os_user_password_change(window):
    import re
    db = window.db
    sid = db.add_server(None, "Сервер")
    storage = db.get_server(sid)
    storage["payload"] = {"v": 1, "os_users": [
        {"login": "root", "password": "old-pass", "role": "root", "label": ""}]}
    db.save_server(sid, storage)
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    users = dict(window.server_tabs.list_fields())["os_users"]
    users.set_items([{"login": "root", "password": "new-pass", "role": "root", "label": ""}])
    window.save_current()

    notes = db.get_server(sid)["payload"]["notes"]
    assert re.fullmatch(
        rf"Пароль old-pass от пользователя root изменен: {_HISTORY_STAMP_RE}", notes)
    assert window.server_tabs.f_notes.get_text() == notes


def test_server_password_history_records_panel_password_change(window):
    import re
    db = window.db
    sid = db.add_server(None, "Сервер")
    storage = db.get_server(sid)
    storage["payload"] = {"v": 1, "panels": [
        {"panel_type": "Hestia", "url": "https://1.2.3.4",
         "login": "admin", "password": "old-panel-pass"}]}
    db.save_server(sid, storage)
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    panels = dict(window.server_tabs.list_fields())["panels"]
    panels.set_items([{"panel_type": "Hestia", "url": "https://1.2.3.4",
                       "login": "admin", "password": "new-panel-pass"}])
    window.save_current()

    notes = db.get_server(sid)["payload"]["notes"]
    assert re.fullmatch(
        rf"Пароль old-panel-pass от панели Hestia с логином admin "
        rf"изменен: {_HISTORY_STAMP_RE}", notes)


def test_server_password_history_both_triggers_produce_two_lines(window):
    db = window.db
    sid = db.add_server(None, "Сервер")
    storage = db.get_server(sid)
    storage["payload"] = {
        "v": 1,
        "os_users": [{"login": "root", "password": "u-old", "role": "root", "label": ""}],
        "panels": [{"panel_type": "3x-ui", "url": "", "login": "admin", "password": "p-old"}],
    }
    db.save_server(sid, storage)
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    dict(window.server_tabs.list_fields())["os_users"].set_items(
        [{"login": "root", "password": "u-new", "role": "root", "label": ""}])
    dict(window.server_tabs.list_fields())["panels"].set_items(
        [{"panel_type": "3x-ui", "url": "", "login": "admin", "password": "p-new"}])
    window.save_current()

    lines = db.get_server(sid)["payload"]["notes"].splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("Пароль u-old от пользователя root изменен:")
    assert lines[1].startswith("Пароль p-old от панели 3x-ui с логином admin изменен:")


def test_server_password_history_new_row_no_line(window):
    """Добавление новой строки (логин не совпадает ни с одной старой) —
    механизм не срабатывает, даже если первичный пароль непустой."""
    db = window.db
    sid = db.add_server(None, "Сервер")
    storage = db.get_server(sid)
    storage["payload"] = {"v": 1, "os_users": [
        {"login": "root", "password": "root-pass", "role": "root", "label": ""}]}
    db.save_server(sid, storage)
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    users = dict(window.server_tabs.list_fields())["os_users"]
    users.set_items([
        {"login": "root", "password": "root-pass", "role": "root", "label": ""},
        {"login": "deploy", "password": "brand-new", "role": "user", "label": ""}])
    window.save_current()

    assert db.get_server(sid)["payload"].get("notes", "") == ""


def test_server_password_history_login_and_password_change_no_line(window):
    """Логин и пароль сменились одновременно -> считается новой записью,
    история не пишется (то же правило, что закрывает добавление/удаление)."""
    db = window.db
    sid = db.add_server(None, "Сервер")
    storage = db.get_server(sid)
    storage["payload"] = {"v": 1, "os_users": [
        {"login": "root", "password": "old-pass", "role": "root", "label": ""}]}
    db.save_server(sid, storage)
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    users = dict(window.server_tabs.list_fields())["os_users"]
    users.set_items([{"login": "admin", "password": "new-pass", "role": "root", "label": ""}])
    window.save_current()

    assert db.get_server(sid)["payload"].get("notes", "") == ""


def test_server_password_history_empty_old_password_no_line(window):
    """Первое заполнение пароля новой/пустой строки — нечего сохранять."""
    db = window.db
    sid = db.add_server(None, "Сервер")
    storage = db.get_server(sid)
    storage["payload"] = {"v": 1, "os_users": [
        {"login": "root", "password": "", "role": "root", "label": ""}]}
    db.save_server(sid, storage)
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    users = dict(window.server_tabs.list_fields())["os_users"]
    users.set_items([{"login": "root", "password": "first-pass", "role": "root", "label": ""}])
    window.save_current()

    assert db.get_server(sid)["payload"].get("notes", "") == ""


def test_server_password_history_no_change_no_line(window):
    """Сохранение без изменения пароля — заметки не трогает."""
    db = window.db
    sid = db.add_server(None, "Сервер")
    storage = db.get_server(sid)
    storage["payload"] = {"v": 1, "os_users": [
        {"login": "root", "password": "same-pass", "role": "root", "label": ""}]}
    db.save_server(sid, storage)
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()

    window.server_tabs._widgets["hosting"].set_text("Что-то другое")
    window.save_current()

    assert db.get_server(sid)["payload"].get("notes", "") == ""


# ─── Горячие клавиши на серверной карточке (M-01) ──────────────────────────

def test_shortcut_edit_works_on_server_card(window):
    """M-01: шорткат «Редактировать» на открытой VPS-карточке раньше молчал —
    _sc_edit_account() учитывал только _current_account_id/_current_fin.
    Теперь has_target строится через _any_card_open(), edit_current()
    маршрутизирует на server_toggle_edit по MRO ServerCardMixin."""
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()
    window._select_node(SERVER, sid)
    assert not window.is_editing

    window._sc_edit_account()
    assert window.is_editing
    assert window._current_server == (SERVER, sid)


def test_shortcut_generators_do_not_fire_on_server_card(window, monkeypatch):
    """M-01: генераторы пароля/ПД — поля аккаунтной карточки. На открытой
    VPS-карточке (_current_fin остаётся None) старая отрицательная проверка
    `_current_fin is None` ошибочно разрешала их; теперь позитивная проверка
    `_current_account_id is not None` верно запрещает генераторы для сервера."""
    db = window.db
    sid = db.add_server(None, "Сервер")
    window._reload_tree()
    window._select_node(SERVER, sid)
    window.edit_current()
    assert window.is_editing
    assert window._current_account_id is None

    called = {"password": False, "personal": False}
    monkeypatch.setattr(window, "generate_password",
                        lambda: called.__setitem__("password", True))
    monkeypatch.setattr(window, "generate_personal_data",
                        lambda: called.__setitem__("personal", True))

    window._sc_gen_password()
    window._sc_gen_personal()
    assert called["password"] is False
    assert called["personal"] is False
