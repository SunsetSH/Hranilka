"""Карточка VPS-сервера правой панели — примесь ServerCardMixin.

По образцу FinCardMixin (fin_card.py): асинхронная загрузка/сохранение через
run_async с токеном сессии и поколением карточки (_card_gen), кеш
несохранённых правок (stash) в общий _edit_cache по ключу (SERVER, id) —
буквально ("server", id), т.к. nodetypes.SERVER == "server". Код полностью
независим от финансовой инфраструктуры (FIN_TYPES/ItemTypeSpec/FinItemTabs —
docs/ТЗ_VPS_Серверы.md §2): сервер не финансовый лист.

Диспетчер on_item_selected — верхний в MRO MainWindow (перед FinCardMixin,
см. main_window.py): узел SERVER открывает серверную карточку, всё
остальное отдаётся FinCardMixin.on_item_selected без изменений (тот
разбирается между fin-записью и аккаунтом сам)."""

from PySide6.QtCore import Qt, QDateTime

from hranilka.core import domain
from hranilka.core.domain import days_until_paid_until
from hranilka.core.nodetypes import SERVER, ACCOUNT
from hranilka.data.database import CorruptedPayloadError, StaleSessionError
from hranilka.data.models.server_data import ServerData
from hranilka.ui.card_base import CardFlowMixin, LeafCardSpec
from hranilka.ui.fin_card import FinCardMixin
from hranilka.ui import theme
from hranilka.core import util

# Порог предупреждения об оплате (дней) — независим от fin_domain.EXPIRY_WARN_DAYS
# (сервер не финансовый инструмент, docs/ТЗ_VPS_Серверы.md §2).
PAID_WARN_DAYS = 14


async def _server_orphan_links(win, key, storage, session):
    """H-02: get_server() уже вернул account_ids в этом же снимке — второго
    чтения не нужно, окно гонки (и семантика links=None) исчезает."""
    return storage.get("account_ids") or []


_SERVER_SPEC = LeafCardSpec(
    tabs_attr="server_tabs", key_attr="_current_server",
    edit_flag_attr="_server_edit_on_load", edit_flag_value=lambda k: k[1],
    data_attr="current_server_data", data_cls=ServerData,
    entity_name="сервера",
    db_load="get_server", db_link_accounts="get_server_link_accounts",
    db_links="get_server_links", db_save="save_server_with_links",
    exclude_kw="exclude_server_id",
    apply="_apply_server_storage", collect="_collect_server_storage",
    status="_update_server_status",
    marker=lambda w, k: w._refresh_server_marker(k[1]),
    on_saved=lambda w, st: w._resync_server_lists(st.get("payload") or {}),
    update_item_after_save=lambda w, k, st: w._update_server_item_after_save(
        k[1], st),
    err_load="Не удалось загрузить сервер",
    err_save="Не удалось сохранить сервер",
    err_recover=("Не удалось восстановить связи черновика сервера — открытие "
                 "отменено, чтобы не потерять привязки"),
    orphan_log_load="Не удалось подгрузить сервер для осиротевшей загрузки",
    orphan_deleted="Загрузка отменена: сервер удалён",
    after_load=lambda w: w._warn_server_paid_due(),
    fail_closed_excs=(CorruptedPayloadError,),
    fail_closed_msg="Данные сервера повреждены — запись в БД не изменена",
    orphan_links=_server_orphan_links,
)


class ServerCardMixin(CardFlowMixin):
    """Правая панель для VPS-серверов: диспетчер выбора + просмотр/правка."""

    # ----- Диспетчеры общих кнопок (третья ветка после fin/аккаунт) -----

    def edit_current(self):
        if self._current_server is not None:
            self.server_toggle_edit()
        else:
            FinCardMixin.edit_current(self)

    def save_current(self):
        if self._current_server is not None:
            self.server_save()
        else:
            FinCardMixin.save_current(self)

    def cancel_current(self):
        if self._current_server is not None:
            self.server_cancel()
        else:
            FinCardMixin.cancel_current(self)

    # ----- Диспетчер выбора узла -----

    def on_item_selected(self, current, previous):
        """Единая точка входа currentItemChanged (верхняя в MRO). Узел SERVER
        открывает ServerTabs, всё остальное отдаётся FinCardMixin (fin/аккаунт)
        без изменений."""
        node = self._node(current)
        ntype = node["type"] if node else None
        new_server_id = node["id"] if ntype == SERVER else None

        # Уходим с редактируемой серверной карточки на другой узел → стэшим.
        if (self.is_editing and self._current_server is not None
                and self._current_server[1] != new_server_id):
            self._stash_current_server_edits()
            self._refresh_server_marker(self._current_server[1])

        if ntype == SERVER:
            # Уходим с редактируемой fin-записи/аккаунта на сервер → стэшим их
            # правки тем же путём, что и переключение fin ↔ аккаунт (см.
            # FinCardMixin.on_item_selected) — здесь третья ветка диспетчера.
            if self.is_editing and self._current_fin is not None:
                self._stash_current_fin_edits()
                self._refresh_fin_marker(*self._current_fin)
            elif self.is_editing and self._current_account_id is not None:
                self._stash_current_edits(self._current_account_id)
                self._refresh_dirty_markers(self._current_account_id)
            self._current_fin = None
            self._current_account_id = None
            self._open_server_card(current, node)
            return

        # Цель — fin-запись/аккаунт/контейнер/пусто: управление у FinCardMixin.
        self._current_server = None
        FinCardMixin.on_item_selected(self, current, previous)

    def _open_server_card(self, current, node):
        item_id = node["id"]
        self._drop_stale_edit_flag("_server_edit_on_load", item_id)
        self._card_gen += 1
        gen = self._card_gen
        self.current_tree_item = current
        self._current_server = (SERVER, item_id)
        self.right_stack.setCurrentWidget(self.server_tabs)
        self.fin_banner.hide()
        self._set_card_busy(True)
        util.fire(self._load_server_into_ui(item_id, gen))

    # ----- Асинхронная загрузка -----

    async def _load_server_into_ui(self, item_id, gen):
        await self._leaf_load_async(_SERVER_SPEC, (SERVER, item_id), gen)

    def _apply_server_storage(self, storage, links=None, other_bytes=None):
        """Заполнить ServerTabs из storage (имя, поля, связи с аккаунтами,
        галерея)."""
        self.current_server_data = ServerData.from_storage(storage)
        self.server_tabs.f_name.set_text(self.current_server_data.name)
        self.server_tabs.load_payload(self.current_server_data.payload)
        self.server_tabs.f_linked_accounts.set_data(links or [])
        self._load_server_gallery(self.current_server_data.gallery, other_bytes)

    def _load_server_gallery(self, gallery, other_bytes=None):
        """Загрузить галерею сервера и подключить ленивое чтение BLOB — тот же
        контракт H-6/M7-03, что и у аккаунтной/фин-галереи, без правок gallery.py."""
        widget = self.server_tabs.f_gallery_widget
        widget.set_data(gallery or [])
        widget.set_image_loader(self.db.load_server_gallery_image)
        widget.set_async_reader(self._server_gallery_read_blob, self.db.current_session)
        widget.set_account_provider(lambda: self._current_server)
        widget.set_orphan_upload_handler(self._on_server_gallery_orphan_upload)
        if other_bytes is not None:
            widget.set_size_context(lambda v=other_bytes: v)
        elif self._current_server is not None:
            widget.set_size_context(
                lambda i=self._current_server[1]:
                self.db.gallery_total_bytes(exclude_server_id=i))
        else:
            widget.set_size_context(None)

    async def _server_gallery_read_blob(self, image_id, session):
        try:
            return await self.db.run_async(
                self.db.load_server_gallery_image, image_id, _session=session)
        except StaleSessionError:
            return None

    def _resync_server_lists(self, payload):
        """Перезаполнить спископодобные виджеты (пользователи/ключи/панели/
        Доп. IP) из payload после сохранения — как load_payload при загрузке
        (пустые строки исчезают из интерфейса)."""
        self.server_tabs.resync_lists(payload)

    def _collect_server_storage(self):
        """Собрать storage сервера из полей UI (без записи в БД).

        Обязательное имя проверяется до вызова этого метода в server_save().

        История паролей (docs/ТЗ_VPS_Серверы.md §8.3): если пароль
        пользователя ОС/панели изменился (сопоставление старого/нового
        списков по login / (panel_type, login)) — дописывает строку(и) в
        заметки ДО формирования storage, по образцу
        AccountCardMixin._collect_account_data/_append_password_history."""
        data = self.current_server_data
        old_payload = data.payload
        data.name = self.server_tabs.f_name.get_text().strip()
        new_payload = self.server_tabs.collect_payload()
        new_payload["notes"] = self._append_server_password_history(
            new_payload.get("notes", ""), old_payload, new_payload)
        data.payload = {**old_payload, **new_payload}
        data.gallery = self.server_tabs.f_gallery_widget.get_data()
        return data.to_storage()

    def _append_server_password_history(self, notes, old_payload, new_payload):
        """Если пароль пользователя ОС/панели был непустым и изменился —
        дописывает в конец заметок строку(и) истории (логика —
        domain.append_server_password_history) и обновляет поле заметок в
        интерфейсе. Возвращает итоговый текст заметок."""
        stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        updated = domain.append_server_password_history(
            notes, old_payload, new_payload, stamp)
        if updated != notes:
            self.server_tabs.f_notes.set_text(updated)   # отразить в UI сразу
        return updated

    # ----- Правка / сохранение / отмена -----

    def server_toggle_edit(self):
        self._leaf_toggle_edit(_SERVER_SPEC)

    def server_save(self):
        self._leaf_save(_SERVER_SPEC, self._server_save_async)

    async def _server_save_async(self, gen):
        await self._leaf_save_async(_SERVER_SPEC, gen)

    def server_cancel(self):
        self._leaf_cancel(_SERVER_SPEC, self._server_cancel_async)

    async def _server_cancel_async(self, gen):
        await self._leaf_cancel_async(_SERVER_SPEC, gen)

    # ----- Связи сервер ↔ аккаунт (снизу вкладки «База») -----

    def on_server_account_link_navigate(self, account_id):
        """Клик по привязанному аккаунту на карточке сервера — выделить его
        узел в дереве."""
        self._select_node(ACCOUNT, account_id)

    def on_add_server_account_link_requested(self):
        if self._current_server is None:
            return
        util.fire(self._add_server_account_link_async())

    def _server_link_ctx_valid(self, gen, session, key):
        """Карточка после await та же: поколение, сессия и ключ сервера."""
        return (gen == self._card_gen and session == self.db.current_session()
                and self._current_server == key)

    async def _add_server_account_link_async(self):
        session = self.db.current_session()
        gen = self._card_gen
        key = self._current_server
        try:
            accounts = await self.db.run_async(
                self.db.get_all_accounts, _session=session)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            self._show_card_error("Не удалось загрузить список аккаунтов", e)
            return
        if not self._server_link_ctx_valid(gen, session, key):
            return
        widget = self.server_tabs.f_linked_accounts
        chosen, ok = theme.themed_multiselect(
            self.config, self, "Связать с аккаунтами", accounts,
            widget.get_data())
        if ok and self._server_link_ctx_valid(gen, session, key):
            chosen_set = set(chosen)
            links = [a for a in accounts if a["id"] in chosen_set]
            widget.set_data(links)
            widget.set_editable(self.is_editing)

    # ----- Осиротевшая загрузка галереи -----

    def _on_server_gallery_orphan_upload(self, server_key, desc, data):
        """Импорт картинки стартовал для сервера server_key, но карточку сменили
        (виджет галереи один на все серверы) — адресуем картинку своему серверу."""
        self._leaf_orphan_upload(_SERVER_SPEC, server_key, desc, data,
                                 self._server_orphan_into_new_draft)

    async def _server_orphan_into_new_draft(self, server_key, desc, data):
        await self._leaf_orphan_into_new_draft(
            _SERVER_SPEC, server_key, desc, data)

    def _mark_server_orphan_dirty(self, server_key):
        self._leaf_mark_dirty(_SERVER_SPEC, server_key)

    # ----- Кеш несохранённых правок -----

    def _stash_current_server_edits(self):
        self._leaf_stash(_SERVER_SPEC)

    # ----- Плашка/статус/дерево -----

    def _warn_server_paid_due(self):
        """Уведомление об оплате в статус-баре при открытии карточки — по
        образцу AccountCardMixin._warn_password_due (account_card.py):
        транзитное сообщение, не персистентная плашка в правой панели
        (баннер-слот fin_banner для серверов больше не используется,
        УИ §2026-07-15). Источник days — узел дерева (paid_days_left,
        tree_ops.py), а не пересчёт по payload."""
        node = self._node(self.current_tree_item)
        days = node.get("paid_days_left") if node else None
        if days is None:
            return
        if days < 0:
            self.statusBar().showMessage("ОПЛАТА СЕРВЕРА ПРОСРОЧЕНА!", 5000)
        elif days <= PAID_WARN_DAYS:
            self.statusBar().showMessage(f"Оплатить сервер через {days} дн.", 4000)

    def _update_server_status(self):
        if self._current_server is None:
            return
        prefix = "[РЕД] " if self.is_editing else "[ПРОСМОТР] "
        name = self.current_server_data.name if self.current_server_data else ""
        self.status_info.setText(prefix + name)

    def _refresh_server_marker(self, server_id):
        """Точечно перерисовать узел сервера (маркер несохранённых правок)."""
        item = self._find_leaf_item(SERVER, server_id)
        if item is not None:
            self._apply_item_style(item, self._node(item))

    def _update_server_item_after_save(self, server_id, storage):
        """Обновить узел сервера после сохранения без полной перестройки
        дерева: имя, «оплачен до» и снятие маркера правок (по образцу
        FinCardMixin._update_fin_item_after_save)."""
        item = self._find_leaf_item(SERVER, server_id)
        if item is None:
            self._reload_tree()
            return
        node = self._node(item)
        new_name = storage.get("name") or node["name"]
        name_changed = node["name"] != new_name
        paid_until = (storage.get("payload") or {}).get("paid_until")
        days = days_until_paid_until(paid_until)
        node.update({"name": new_name, "paid_days_left": days})
        item.setData(0, Qt.UserRole, node)
        item.setToolTip(0, new_name)
        self._apply_item_style(item, node)
        if self.sort_mode == "name" and name_changed:
            self._reload_tree()
        elif self.search_text:
            self._apply_filter()
