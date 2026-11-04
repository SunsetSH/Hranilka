"""ServerTabs — вкладки и поля карточки VPS-сервера (docs/ТЗ_VPS_Серверы.md §4).

Построена вручную по образцу AccountTabs (ui/tabs.py) и по структуре
FinItemTabs (ui/fin_tabs.py: fields()/list_fields()/set_all_editable()), но
полностью независимым кодом — сервер не финансовый инструмент (docs §2),
FIN_TYPES/ItemTypeSpec/FinItemTabs не используются и не изменяются. Разрешено
переиспользовать только нейтральные строительные блоки: FieldSpec (контракт
строк KeyValueListWidget), CopyableField/CopyableTextEdit/CopyableDateField
(fields.py), LinkedAccountsWidget, GalleryWidget, wrap_scrollable.

Все вкладки — через прокрутку (_add_scroll_tab, как у AccountTabs/FinItemTabs).
"""
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QDialog)

from hranilka.core.fin_types import FieldSpec
from hranilka.data.models.server_data import OS_USER_KEYS, SSH_KEY_KEYS, PANEL_KEYS
from hranilka.generators import password_gen
from hranilka.ui.generator_dialog import GeneratorSettingsDialog
from hranilka.ui.tabs import PayloadTabs
from hranilka.ui.widgets.fields import CopyableIsoDateField
from hranilka.ui.widgets import (CopyableField,
                                 CopyableTextEdit, KeyValueListWidget, heading_label)


# Списковые секции карточки: ключи item_fields ДОЛЖНЫ совпадать с ключами
# толерантного чтения ServerData (OS_USER_KEYS/SSH_KEY_KEYS/PANEL_KEYS,
# data/models/server_data.py) — единственная связь между слоями, проверяется
# round-trip тестом.
_OS_USER_FIELDS = (
    # Два ряда (row_group, как у панелей): ряд 1 — логин+пароль (оба со своей
    # [КОП] — copy_btn=True), ряд 2 — роль+метка. Общая [КОП] строки
    # крепится к первому ряду и там же оказалось бы поле-«значение»
    # (пароль, единственный secret) со своей индивидуальной [КОП] — буквальный
    # визуальный дубль; KeyValueListWidget подавляет её сам
    # (_suppress_general_copy) — del_btn остаётся. gen_btn: под полями строки —
    # «СГЕНЕРИРОВАТЬ ПАРОЛЬ»/«ПАРАМЕТРЫ ГЕНЕРАЦИИ», тем же генератором и
    # настройками, что и у аккаунта (УИ §2026-07-16).
    FieldSpec("login", "Логин", "text", "Пользователи", row_group="r1",
              copy_btn=True),
    FieldSpec("password", "Пароль", "text", "Пользователи", secret=True,
              row_group="r1", copy_btn=True, gen_btn=True),
    FieldSpec("role", "Роль", "enum", "Пользователи", row_group="r2",
              options=("root", "sudo", "user")),
    FieldSpec("label", "Метка", "text", "Пользователи", row_group="r2"),
)
assert tuple(f.key for f in _OS_USER_FIELDS) == OS_USER_KEYS

_SSH_KEY_FIELDS = (
    FieldSpec("label", "Метка", "text", "SSH-ключи"),
    FieldSpec("key_type", "Тип ключа", "enum", "SSH-ключи",
              options=("ed25519", "rsa", "ecdsa", "other")),
    FieldSpec("public_key", "Публичный ключ", "multiline", "SSH-ключи"),
    FieldSpec("private_key", "Приватный ключ", "multiline", "SSH-ключи",
              secret=True),
    FieldSpec("passphrase", "Passphrase", "text", "SSH-ключи", secret=True),
)
assert tuple(f.key for f in _SSH_KEY_FIELDS) == SSH_KEY_KEYS


# Панели — два ряда (row_group, УИ §2026-07-15): ряд 1 «Тип панели» (узкое,
# по ширине подсказки) + «URL» (широкое, стрейч, [КОП]/[X] в конце ряда);
# ряд 2 «Логин»/«Пароль». port/label убраны из панелей.
# Логин/пароль — во втором row_group-ряду, до которого общая [КОП] строки
# (крепится к первому ряду) визуально не достаёт — поэтому у обоих своя
# индивидуальная [КОП] (copy_btn); пароль дополнительно получает пару кнопок
# под полями строки (gen_btn) — тот же генератор/настройки, что у аккаунта
# (УИ §2026-07-16).
_PANEL_FIELDS = (
    FieldSpec("panel_type", "Тип панели", "enum", "Панели", row_group="r1",
              narrow=True,
              options=("3x-ui", "x-ui", "Marzban", "Hestia", "aaPanel",
                       "ISPmanager", "другое")),
    FieldSpec("url", "URL", "text", "Панели", row_group="r1"),
    FieldSpec("login", "Логин", "text", "Панели", row_group="r2",
              copy_btn=True),
    FieldSpec("password", "Пароль", "text", "Панели", secret=True,
              row_group="r2", copy_btn=True, gen_btn=True),
)
assert tuple(f.key for f in _PANEL_FIELDS) == PANEL_KEYS

# «Доп. IP» — список однострочных полей (УИ §2026-07-15): KeyValueListWidget
# с одним полем "ip"; payload extra_ips хранит список строк (конверсия в
# _extra_ips_to_items/_extra_ips_from_items ниже).
_EXTRA_IP_FIELDS = (
    FieldSpec("ip", "IP", "text", "База"),
)


def _extra_ips_to_items(ips) -> list[dict]:
    """payload extra_ips (список строк, уже толерантно нормализован
    ServerData) -> представление KeyValueListWidget."""
    if not isinstance(ips, list):
        return []
    return [{"ip": str(ip)} for ip in ips if str(ip or "").strip()]


def _extra_ips_from_items(items: list[dict]) -> list[str]:
    """Представление KeyValueListWidget -> payload extra_ips (список строк)."""
    return [str(it.get("ip", "")).strip() for it in items
            if str(it.get("ip", "") or "").strip()]


# Скалярные поля payload вкладки «База» (ключи — как в server_data.py).
# extra_ips сюда не входит — списковое поле, свой виджет (_extra_ips_widget).
_SCALAR_KEYS = ("hosting", "host", "ssh_port", "price", "os_name",
                "location", "paid_until", "notes")


class ServerTabs(PayloadTabs):
    LINKS_HEADING = "ПРИВЯЗАН К АККАУНТАМ:"

    """Карточка VPS-сервера: База / Пользователи / SSH-ключи / Панели /
    Заметки / Галерея."""

    def __init__(self, parent=None, config=None):
        super().__init__(parent, config)
        self.build_tabs()

    def build_tabs(self):
        self._add_scroll_tab(self._build_base_tab(), "База")
        self._add_scroll_tab(self._build_list_tab(
            "os_users", _OS_USER_FIELDS, "Пользователи ОС",
            gen_settings=True), "Пользователи")
        self._add_scroll_tab(self._build_list_tab(
            "ssh_keys", _SSH_KEY_FIELDS, "SSH-ключи"), "SSH-ключи")
        self._add_scroll_tab(self._build_list_tab(
            "panels", _PANEL_FIELDS, "Панели управления",
            gen_settings=True), "Панели")
        self._add_scroll_tab(self._build_notes_tab(), "Заметки")
        self._add_scroll_tab(self._build_gallery_tab(), "Галерея")

    # ----- Вкладка «База» -----

    def _row(self, page, left_key, left_label, left_widget,
            right_key, right_label, right_widget):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        for key, label, widget in ((left_key, left_label, left_widget),
                                   (right_key, right_label, right_widget)):
            column_index = row.count()
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(4)
            heading = heading_label(label + ":")
            col.addWidget(heading)
            col.addWidget(widget)
            row.addLayout(col, 1)
            self._widgets[key] = widget
            self.register_empty_section(
                page, (heading, widget),
                lambda w=widget: bool(w.get_text().strip()),
                visibility_changed=lambda shown, r=row, i=column_index:
                    r.setStretch(i, 1 if shown else 0))
        return row

    def _build_base_tab(self):
        w = QWidget(self)
        layout = QVBoxLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 8, 0, 0)

        self.f_name = CopyableField()
        name_label = heading_label("Название (обязательно):")
        layout.addWidget(name_label)
        layout.addWidget(self.f_name)
        self.register_empty_section(
            w, (name_label, self.f_name),
            lambda: bool(self.f_name.get_text().strip()), required=True)

        hosting = CopyableField()
        self._widgets["hosting"] = hosting
        hosting_label = heading_label("Провайдер / тариф:")
        layout.addWidget(hosting_label)
        layout.addWidget(hosting)
        self.register_empty_section(
            w, (hosting_label, hosting),
            lambda: bool(hosting.get_text().strip()))

        layout.addLayout(self._row(w,
            "paid_until", "Оплачен до", CopyableIsoDateField(),
            "price", "Стоимость", CopyableField()))

        layout.addLayout(self._row(w,
            "host", "Хост / IP", CopyableField(),
            "ssh_port", "Порт SSH", CopyableField()))

        layout.addLayout(self._row(w,
            "os_name", "ОС", CopyableField(),
            "location", "Локация", CopyableField()))

        self._extra_ips_widget = KeyValueListWidget(
            _EXTRA_IP_FIELDS, config=self.config)
        ips_label = heading_label("Доп. IP:")
        layout.addWidget(ips_label)
        layout.addWidget(self._extra_ips_widget)
        self.register_empty_section(
            w, (ips_label, self._extra_ips_widget),
            lambda: bool(self._extra_ips_widget.get_items()))

        self._add_links_section(w, layout)
        layout.addStretch()
        return w

    # ----- Списковые вкладки (пользователи/ключи/панели) -----

    def _build_list_tab(self, key, item_fields, heading, gen_settings=False):
        """gen_settings=True — вкладка со строками-паролями (Пользователи/
        Панели, УИ §2026-07-16, как в карточке аккаунта — ui/tabs.py):
        gen_callback/gen_settings_callback в KeyValueListWidget рендерят под
        полями КАЖДОГО экземпляра строки пару кнопок «СГЕНЕРИРОВАТЬ ПАРОЛЬ» +
        «ПАРАМЕТРЫ ГЕНЕРАЦИИ» (FieldSpec.gen_btn) — больше не одна кнопка
        настроек на всю вкладку."""
        w = QWidget(self)
        layout = QVBoxLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 8, 0, 0)
        label = heading_label(heading + ":")
        layout.addWidget(label)
        if gen_settings:
            list_widget = KeyValueListWidget(
                item_fields, config=self.config,
                copy_key="url" if key == "panels" else None,
                gen_callback=self._generate_secret,
                gen_settings_callback=self._open_gen_settings_for_row)
        else:
            list_widget = KeyValueListWidget(item_fields, config=self.config)
        self._list_widgets[key] = list_widget
        layout.addWidget(list_widget)
        self.register_empty_section(
            w, (label, list_widget),
            lambda lw=list_widget: bool(lw.get_items()))
        layout.addStretch()
        return w

    def _generate_secret(self) -> str:
        """Секрет по ЕДИНЫМ настройкам генератора (тот же config-ключ, что у
        аккаунта) — kv_list не знает про password_gen, только зовёт этот
        callable (gen_callback)."""
        return password_gen.generate_from_config(self.config)

    def _open_gen_settings_for_row(self) -> str | None:
        """«ПАРАМЕТРЫ ГЕНЕРАЦИИ» конкретного экземпляра строки (пользователь/
        панель): тот же диалог, что у аккаунта (ui/generator_dialog.py) — при
        OK диалог уже записал настройки в config, здесь сохраняем файл и
        возвращаем пароль предпросмотра для подстановки в поле пароля ЭТОЙ
        строки (идентично AccountCardMixin.open_password_generator_settings).
        При отмене — None, kv_list поле не трогает."""
        dlg = GeneratorSettingsDialog(self.config, self)
        if dlg.exec() == QDialog.Accepted:
            self.config.save()
            return dlg.preview_text()
        return None

    # ----- Заметки / Галерея -----

    def _build_notes_tab(self):
        w = QWidget(self)
        layout = QVBoxLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 8, 0, 0)
        notes = CopyableTextEdit()
        self.f_notes = notes
        self._widgets["notes"] = notes
        label = heading_label("Заметки:")
        layout.addWidget(label)
        layout.addWidget(notes)
        self.register_empty_section(
            w, (label, notes), lambda: bool(notes.get_text().strip()))
        return w

    # ----- Контракт (по образцу FinItemTabs) -----

    def _field_keys(self):
        return iter(_SCALAR_KEYS)

    def _list_keys(self):
        return iter(("os_users", "ssh_keys", "panels"))

    def _extra_editable_widgets(self):
        return [self._extra_ips_widget]

    def _load_extra(self, payload: dict) -> None:
        self._extra_ips_widget.set_items(
            _extra_ips_to_items(payload.get("extra_ips")))

    def _collect_extra(self, payload: dict) -> None:
        payload["extra_ips"] = _extra_ips_from_items(
            self._extra_ips_widget.get_items())

    def resync_lists(self, payload: dict) -> None:
        """Пересобрать спископодобные виджеты (пользователи/ключи/панели/
        Доп. IP) из payload после сохранения — как load_payload при загрузке
        (пустые строки исчезают и из интерфейса, не дожидаясь перезагрузки)."""
        self._load_lists(payload)
        self._load_extra(payload)
