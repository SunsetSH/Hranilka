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
import logging

from PySide6.QtCore import Qt, QDateTime

from hranilka.core import domain
from hranilka.core.domain import days_until_paid_until
from hranilka.core.nodetypes import SERVER, ACCOUNT
from hranilka.data.database import CorruptedPayloadError, StaleSessionError
from hranilka.data.models.server_data import ServerData
from hranilka.ui.fin_card import FinCardMixin
from hranilka.ui import theme
from hranilka.core import util

# Порог предупреждения об оплате (дней) — независим от fin_domain.EXPIRY_WARN_DAYS
# (сервер не финансовый инструмент, docs/ТЗ_VPS_Серверы.md §2).
PAID_WARN_DAYS = 14


class ServerCardMixin:
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
        if (self._server_edit_on_load is not None
                and self._server_edit_on_load != item_id):
            self._server_edit_on_load = None
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
        session = self.db.current_session()
        key = (SERVER, item_id)
        try:
            if key in self._edit_cache:
                cached = self._edit_cache[key]
                link_ids = cached.get("links")
                if link_ids is None:
                    # H-02: связи неизвестны (черновик пережил сбой чтения).
                    # НЕ подменяем молча пустым списком — обычное сохранение
                    # синхронизировало бы server_links с [] и стёрло бы
                    # реальные привязки. Пытаемся перечитать актуальный снимок
                    # из БД; вторая неудача — открываем карточку fail-closed
                    # (по образцу CorruptedPayloadError ниже), черновик в
                    # _edit_cache не трогаем.
                    try:
                        link_ids = await self.db.run_async(
                            self.db.get_server_links, item_id, _session=session)
                    except StaleSessionError:
                        return
                    except Exception as e:            # noqa: BLE001
                        if gen == self._card_gen:
                            self._current_server = None
                            self.is_editing = False
                            self._show_placeholder()
                            self._show_card_error(
                                "Не удалось восстановить связи черновика "
                                "сервера — открытие отменено, чтобы не "
                                "потерять привязки", e)
                        return
                    if gen != self._card_gen:
                        return
                    cached["links"] = link_ids        # связи стали известны
                links = await self.db.run_async(
                    self._resolve_link_names, link_ids, _session=session)
                other_bytes = await self.db.run_async(
                    self.db.gallery_total_bytes, exclude_server_id=item_id,
                    _session=session)
                if gen != self._card_gen:
                    return
                self._apply_server_storage(cached["storage"], links, other_bytes)
                self.is_editing = True
                self.server_tabs.set_all_editable(True)
                self.edit_btn.hide(); self.save_btn.show(); self.cancel_btn.show()
            else:
                storage = await self.db.run_async(
                    self.db.get_server, item_id, _session=session)
                if gen != self._card_gen:
                    return
                if storage is None:
                    # Сервер исчез между выбором и загрузкой — заглушка.
                    self._current_server = None
                    self.is_editing = False
                    self._show_placeholder()
                    self._update_server_status()
                    return
                links = await self.db.run_async(
                    self.db.get_server_link_accounts, item_id, _session=session)
                other_bytes = await self.db.run_async(
                    self.db.gallery_total_bytes, exclude_server_id=item_id,
                    _session=session)
                if gen != self._card_gen:
                    return
                self._apply_server_storage(storage, links, other_bytes)
                self.is_editing = False
                self.server_tabs.set_all_editable(False)
                self.edit_btn.show(); self.save_btn.hide(); self.cancel_btn.hide()
                if self._server_edit_on_load == item_id:
                    # Только что созданный сервер — сразу в режим правки.
                    self._server_edit_on_load = None
                    self.is_editing = True
                    self.server_tabs.set_all_editable(True)
                    self.edit_btn.hide(); self.save_btn.show(); self.cancel_btn.show()
            self._update_server_status()
            self._warn_server_paid_due()
        except StaleSessionError:
            return                               # БД сменена (lock/restore) — молча
        except CorruptedPayloadError as e:
            # M-03: JSON payload сервера повреждён — fail-closed. Карточку НЕ
            # открываем (иначе обычное сохранение стёрло бы потенциально
            # восстановимую строку в БД пустым payload); строка в БД остаётся
            # нетронутой. Заглушка + сообщение вместо тихой пустой карточки.
            if gen == self._card_gen:
                self._current_server = None
                self.is_editing = False
                self._show_placeholder()
                self._show_card_error(
                    "Данные сервера повреждены — запись в БД не изменена", e)
        except Exception as e:                       # noqa: BLE001
            if gen == self._card_gen:
                self._show_card_error("Не удалось загрузить сервер", e)
        finally:
            if gen == self._card_gen:
                self._set_card_busy(False)

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
        """Собрать storage сервера из полей UI (без записи в БД). Пустое имя
        не сохраняем — оставляем прежнее (сервер без имени недопустим).

        История паролей (docs/ТЗ_VPS_Серверы.md §8.3): если пароль
        пользователя ОС/панели изменился (сопоставление старого/нового
        списков по login / (panel_type, login)) — дописывает строку(и) в
        заметки ДО формирования storage, по образцу
        AccountCardMixin._collect_account_data/_append_password_history."""
        data = self.current_server_data
        old_payload = data.payload
        name = self.server_tabs.f_name.get_text().strip()
        if name:
            data.name = name
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
        if self._card_busy:
            return
        self.is_editing = True
        self.server_tabs.set_all_editable(True)
        self.edit_btn.hide(); self.save_btn.show(); self.cancel_btn.show()
        self._update_server_status()

    def server_save(self):
        if self._card_busy:
            return
        self._set_card_busy(True)
        util.fire(self._server_save_async(self._card_gen))

    async def _server_save_async(self, gen):
        session = self.db.current_session()
        server = self._current_server
        if server is None:
            self._set_card_busy(False)
            return
        _node_type, item_id = server
        self.server_tabs.set_all_editable(False)
        gallery = self.server_tabs.f_gallery_widget
        if gallery.has_pending_uploads():
            self.statusBar().showMessage("Дождитесь загрузки изображений…", 2000)
            await gallery.wait_pending_uploads()
            if gen != self._card_gen or self._current_server != server:
                return
        storage = self._collect_server_storage()
        link_ids = self.server_tabs.f_linked_accounts.get_data()
        try:
            gallery_ids = await self.db.run_async(
                self.db.save_server_with_links, item_id, storage, link_ids,
                _session=session)
        except StaleSessionError:
            if gen == self._card_gen:
                self._edit_cache[server] = {"storage": storage, "links": link_ids}
                self._dirty_ids.add(server)
                self._refresh_server_marker(item_id)
                self.server_tabs.set_all_editable(True)
                self._set_card_busy(False)
                self.statusBar().showMessage(
                    "База была переоткрыта — сохранение не выполнено, "
                    "правки в черновике. Повторите сохранение.", 6000)
            return
        except Exception as e:                       # noqa: BLE001
            if gen == self._card_gen:
                self._edit_cache[server] = {"storage": storage, "links": link_ids}
                self._dirty_ids.add(server)
                self._refresh_server_marker(item_id)
                self.server_tabs.set_all_editable(True)
                self._show_card_error("Не удалось сохранить сервер", e)
                self._set_card_busy(False)
            return

        self._any_db_changes = True
        if gen == self._card_gen:
            gallery.assign_saved_ids(gallery_ids)
            self._edit_cache.pop(server, None)
            self._dirty_ids.discard(server)
            self.is_editing = False
            self.server_tabs.set_all_editable(False)
            self.edit_btn.show(); self.save_btn.hide(); self.cancel_btn.hide()
            self._resync_server_lists(storage.get("payload") or {})
            self._set_card_busy(False)
        self._update_server_item_after_save(item_id, storage)
        self.statusBar().showMessage("СОХРАНЕНО!", 2000)

    def server_cancel(self):
        if self._card_busy:
            return
        self._set_card_busy(True)
        util.fire(self._server_cancel_async(self._card_gen))

    async def _server_cancel_async(self, gen):
        session = self.db.current_session()
        server = self._current_server
        if server is None:
            self._set_card_busy(False)
            return
        _node_type, item_id = server
        self._edit_cache.pop(server, None)
        self._dirty_ids.discard(server)
        self.is_editing = False
        try:
            storage = await self.db.run_async(
                self.db.get_server, item_id, _session=session)
            if gen != self._card_gen:
                return
            if storage is None:
                self._current_server = None
                self._show_placeholder()
                self._update_server_status()
                self._reload_tree()
                return
            links = await self.db.run_async(
                self.db.get_server_link_accounts, item_id, _session=session)
            if gen != self._card_gen:
                return
            self._apply_server_storage(storage, links)
            self.server_tabs.set_all_editable(False)
            self.edit_btn.show(); self.save_btn.hide(); self.cancel_btn.hide()
            self._update_server_status()
            self._refresh_server_marker(item_id)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            if gen == self._card_gen:
                self._show_card_error("Не удалось отменить правки", e)
        finally:
            if gen == self._card_gen:
                self._set_card_busy(False)

    # ----- Связи сервер ↔ аккаунт (снизу вкладки «База») -----

    def on_server_account_link_navigate(self, account_id):
        """Клик по привязанному аккаунту на карточке сервера — выделить его
        узел в дереве."""
        self._select_node(ACCOUNT, account_id)

    def on_add_server_account_link_requested(self):
        if self._current_server is None:
            return
        util.fire(self._add_server_account_link_async())

    async def _add_server_account_link_async(self):
        session = self.db.current_session()
        try:
            accounts = await self.db.run_async(
                self.db.get_all_accounts, _session=session)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            self._show_card_error("Не удалось загрузить список аккаунтов", e)
            return
        widget = self.server_tabs.f_linked_accounts
        chosen, ok = theme.themed_multiselect(
            self.config, self, "Связать с аккаунтами", accounts,
            widget.get_data())
        if ok:
            chosen_set = set(chosen)
            links = [a for a in accounts if a["id"] in chosen_set]
            widget.set_data(links)
            widget.set_editable(self.is_editing)

    # ----- Осиротевшая загрузка галереи -----

    def _on_server_gallery_orphan_upload(self, server_key, desc, data):
        """Импорт картинки стартовал для сервера server_key, но к завершению
        карточку сменили (виджет галереи один на все серверы). Не теряем
        картинку — адресуем её своему серверу (по образцу
        FinCardMixin._on_fin_gallery_orphan_upload)."""
        if server_key is None:
            return
        if server_key == self._current_server and self.is_editing:
            self.server_tabs.f_gallery_widget.add_item(data, desc)
            self._mark_server_orphan_dirty(server_key)
            return
        if server_key in self._edit_cache:
            self._edit_cache[server_key]["storage"].setdefault("gallery", []).append(
                {"desc": desc, "data": data, "image_id": None, "blob_size": None})
            self._mark_server_orphan_dirty(server_key)
            return
        util.fire(self._server_orphan_into_new_draft(server_key, desc, data))

    async def _server_orphan_into_new_draft(self, server_key, desc, data):
        _node_type, item_id = server_key
        session = self.db.current_session()
        try:
            storage = await self.db.run_async(
                self.db.get_server, item_id, _session=session)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            logging.error("Не удалось подгрузить сервер для осиротевшей "
                          "загрузки: %s", e, exc_info=e)
            return
        if storage is None:
            self.statusBar().showMessage("Загрузка отменена: сервер удалён", 4000)
            return
        if server_key == self._current_server and self.is_editing:
            self.server_tabs.f_gallery_widget.add_item(data, desc)
        elif server_key in self._edit_cache:
            self._edit_cache[server_key]["storage"].setdefault("gallery", []).append(
                {"desc": desc, "data": data, "image_id": None, "blob_size": None})
        else:
            srv_data = ServerData.from_storage(storage)
            srv_data.gallery = list(srv_data.gallery) + [
                {"desc": desc, "data": data, "image_id": None, "blob_size": None}]
            # H-02: get_server() уже вернул account_ids в этом же снимке —
            # второе отдельное чтение get_server_links не нужно, окно гонки
            # между двумя SELECT (и вместе с ним семантика links=None для
            # этого пути) исчезает по построению.
            link_ids = storage.get("account_ids") or []
            self._edit_cache[server_key] = {
                "storage": srv_data.to_storage(), "links": link_ids}
        self._mark_server_orphan_dirty(server_key)

    def _mark_server_orphan_dirty(self, server_key):
        self._dirty_ids.add(server_key)
        self._refresh_server_marker(server_key[1])

    # ----- Кеш несохранённых правок -----

    def _stash_current_server_edits(self):
        if self._current_server is None:
            return
        storage = self._collect_server_storage()
        self._edit_cache[self._current_server] = {
            "storage": storage,
            "links": self.server_tabs.f_linked_accounts.get_data()}
        self._dirty_ids.add(self._current_server)

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
