"""Карточка финансовой записи (карта) правой панели — примесь FinCardMixin.

По образцу AccountCardMixin: асинхронная загрузка/сохранение через run_async с
токеном сессии и поколением карточки (_card_gen), кеш несохранённых правок (stash)
в общий _edit_cache по ключу (node_type, id), обработка StaleSessionError.

Роутинг правой панели по типу выбранного узла: on_item_selected здесь —
диспетчер, аккаунтные узлы делегируются AccountCardMixin.on_item_selected
(его логика не меняется), финансовые листья открываются этой примесью.
"""
import logging

from PySide6.QtCore import Qt

from hranilka.core.fin_domain import days_until_expiry, EXPIRY_WARN_DAYS
from hranilka.core.fin_types import FIN_TYPES
from hranilka.core.nodetypes import FIN_LEAF_TYPES
from hranilka.data.database import StaleSessionError
from hranilka.data.models.fin_item import FinItemData
from hranilka.ui.account_card import AccountCardMixin
from hranilka.ui import theme
from hranilka.core import util


class FinCardMixin:
    """Правая панель для финансовых записей: диспетчер выбора + просмотр/правка."""

    # ----- Диспетчер выбора узла -----

    def on_item_selected(self, current, previous):
        """Единая точка входа currentItemChanged. Финансовый лист открывает
        FinItemTabs, всё остальное (аккаунт/папка/сервис/пусто) отдаётся логике
        аккаунта без изменений."""
        node = self._node(current)
        ntype = node["type"] if node else None
        new_fin_id = node["id"] if ntype in FIN_LEAF_TYPES else None

        # Уходим с редактируемой fin-карточки на другой узел → стэшим правки.
        if (self.is_editing and self._current_fin is not None
                and self._current_fin[1] != new_fin_id):
            self._stash_current_fin_edits()
            self._refresh_fin_marker(*self._current_fin)

        if ntype in FIN_LEAF_TYPES:
            # Уходим с редактируемого аккаунта → стэшим его (логика аккаунта).
            if self.is_editing and self._current_account_id is not None:
                self._stash_current_edits(self._current_account_id)
                self._refresh_dirty_markers(self._current_account_id)
            self._current_account_id = None
            self._open_fin_card(current, node)
            return

        # Цель — аккаунт/контейнер/пусто: управление у логики аккаунта.
        self._current_fin = None
        AccountCardMixin.on_item_selected(self, current, previous)

    def _open_fin_card(self, current, node):
        node_type, item_id = node["type"], node["id"]
        # Одноразовый флаг «открыть в правке» действует только для своей записи.
        if (self._fin_edit_on_load is not None
                and self._fin_edit_on_load != (node_type, item_id)):
            self._fin_edit_on_load = None
        # Новое поколение карточки: устаревшие async-результаты не тронут UI.
        self._card_gen += 1
        gen = self._card_gen
        self.current_tree_item = current
        self._current_fin = (node_type, item_id)
        # Страница стека — вкладки типа записи (по FinItemTabs на тип, ключ —
        # node_type узла; сегодня соответствие типов 1:1).
        self.fin_tabs = self.fin_tabs_by_type[node_type]
        self.right_stack.setCurrentWidget(self.fin_tabs)
        self._set_card_busy(True)
        util.fire(self._load_fin_into_ui(node_type, item_id, gen))

    # ----- Асинхронная загрузка -----

    async def _load_fin_into_ui(self, node_type, item_id, gen):
        """Загрузить карточку записи (или восстановить из кеша правок). После
        каждого await сверяем gen: устаревшая загрузка выходит, не трогая UI."""
        session = self.db.current_session()
        try:
            if (node_type, item_id) in self._edit_cache:
                cached = self._edit_cache[(node_type, item_id)]
                link_ids = cached.get("links")
                if link_ids is None:
                    # H-02: связи неизвестны (черновик пережил сбой чтения
                    # get_item_links при осиротевшей загрузке галереи). НЕ
                    # подменяем молча пустым списком — обычное сохранение
                    # синхронизировало бы fin_links с [] и стёрло бы реальные
                    # привязки. load_fin_item не отдаёт связи в одном снимке
                    # (в отличие от get_server), поэтому второе чтение здесь
                    # неизбежно — пытаемся перечитать; вторая неудача —
                    # открываем карточку fail-closed, черновик не трогаем.
                    try:
                        link_ids = await self.db.run_async(
                            self.db.get_item_links, item_id, _session=session)
                    except StaleSessionError:
                        return
                    except Exception as e:            # noqa: BLE001
                        if gen == self._card_gen:
                            self._current_fin = None
                            self.is_editing = False
                            self._show_placeholder()
                            self._show_card_error(
                                "Не удалось восстановить связи черновика "
                                "записи — открытие отменено, чтобы не "
                                "потерять привязки", e)
                        return
                    if gen != self._card_gen:
                        return
                    cached["links"] = link_ids        # связи стали известны
                links = await self.db.run_async(
                    self._resolve_link_names, link_ids, _session=session)
                other_bytes = await self.db.run_async(
                    self.db.gallery_total_bytes, exclude_fin_item_id=item_id,
                    _session=session)
                if gen != self._card_gen:
                    return
                self._apply_fin_storage(cached["storage"], links, other_bytes)
                self.is_editing = True
                self.fin_tabs.set_all_editable(True)
                self.edit_btn.hide(); self.save_btn.show(); self.cancel_btn.show()
            else:
                storage = await self.db.run_async(
                    self.db.load_fin_item, item_id, _session=session)
                if gen != self._card_gen:
                    return
                if storage is None:
                    # Запись исчезла между выбором и загрузкой — заглушка.
                    self._current_fin = None
                    self.is_editing = False
                    self._show_placeholder()
                    self._update_fin_status()
                    return
                links = await self.db.run_async(
                    self.db.get_item_link_accounts, item_id, _session=session)
                other_bytes = await self.db.run_async(
                    self.db.gallery_total_bytes, exclude_fin_item_id=item_id,
                    _session=session)
                if gen != self._card_gen:
                    return
                self._apply_fin_storage(storage, links, other_bytes)
                self.is_editing = False
                self.fin_tabs.set_all_editable(False)
                self.edit_btn.show(); self.save_btn.hide(); self.cancel_btn.hide()
                if self._fin_edit_on_load == (node_type, item_id):
                    # Только что созданная запись — сразу в режим правки.
                    self._fin_edit_on_load = None
                    self.is_editing = True
                    self.fin_tabs.set_all_editable(True)
                    self.edit_btn.hide(); self.save_btn.show(); self.cancel_btn.show()
            self._update_fin_status()
        except StaleSessionError:
            return                               # БД сменена (lock/restore) — молча
        except Exception as e:                       # noqa: BLE001
            if gen == self._card_gen:
                self._show_card_error("Не удалось загрузить запись", e)
        finally:
            if gen == self._card_gen:
                self._set_card_busy(False)

    def _apply_fin_storage(self, storage, links=None, other_bytes=None):
        """Заполнить FinItemTabs из storage (имя, поля, связи с аккаунтами,
        галерея) и обновить плашку срока."""
        self.current_fin_data = FinItemData.from_storage(storage)
        self.fin_tabs.f_name.set_text(self.current_fin_data.name)
        self.fin_tabs.load_payload(self.current_fin_data.payload)
        self.fin_tabs.f_linked_accounts.set_data(links or [])
        self._load_fin_gallery(self.current_fin_data.gallery, other_bytes)
        self._update_fin_banner(self.current_fin_data.item_type,
                                self.current_fin_data.payload)

    def _load_fin_gallery(self, gallery, other_bytes=None):
        """Загрузить галерею записи и подключить ленивое чтение BLOB (§5).
        Контракт H-6/M7-03 переиспользован из аккаунтной галереи без правок
        gallery.py: превью читаются через координатор БД с токеном сессии."""
        widget = self.fin_tabs.f_gallery_widget
        widget.set_data(gallery or [])
        # Синхронный загрузчик — для клика/get_data (БД открыта); фоновый
        # предпросмотр — через координатор БД с токеном сессии (H65-02).
        widget.set_image_loader(self.db.load_fin_gallery_image)
        widget.set_async_reader(self._fin_gallery_read_blob, self.db.current_session)
        # Загрузка картинки переживает смену записи (M7-05): снимаем ключ записи
        # на старте импорта, результат уходит своей записи через orphan-handler.
        widget.set_account_provider(lambda: self._current_fin)
        widget.set_orphan_upload_handler(self._on_fin_gallery_orphan_upload)
        # Финансовая галерея участвует в общем лимите с аккаунтной. В горячем
        # пути сумма прочих BLOB уже получена асинхронно; fallback нужен только
        # для прямых вызовов в тестах/скриптах.
        if other_bytes is not None:
            widget.set_size_context(lambda v=other_bytes: v)
        elif self._current_fin is not None:
            widget.set_size_context(
                lambda i=self._current_fin[1]:
                self.db.gallery_total_bytes(exclude_fin_item_id=i))
        else:
            widget.set_size_context(None)

    async def _fin_gallery_read_blob(self, image_id, session):
        """Async-чтение BLOB фин-галереи для предпросмотра — через координатор БД
        с зафиксированным токеном сессии (H65-02). Смена сессии → None."""
        try:
            return await self.db.run_async(
                self.db.load_fin_gallery_image, image_id, _session=session)
        except StaleSessionError:
            return None

    def _resync_fin_lists(self, payload):
        """Перезаполнить спископодобные виджеты (адреса/ключи) из payload —
        как load_payload при загрузке. Вызывается после успешного сохранения,
        чтобы отфильтрованные (пустые) строки исчезли из интерфейса."""
        for key, list_widget in self.fin_tabs.list_fields():
            value = payload.get(key)
            list_widget.set_items(value if isinstance(value, list) else [])

    def _collect_fin_storage(self):
        """Собрать storage записи из полей UI (без записи в БД).

        Обязательное имя проверяется до вызова этого метода в fin_save().
        Галерея снимается из виджета (контракт H-6/M7-03).

        collect_payload() отдаёт только ключи ТЕКУЩЕГО дескриптора типа —
        сливаем их поверх уже загруженного payload, а не заменяем целиком:
        иначе ключи, которых нет в дескрипторе этой сборки (поле добавлено
        более новой версией — концепт §1 гарантирует толерантное чтение
        именно ради таких случаев), стирались бы при первом же сохранении."""
        data = self.current_fin_data
        data.name = self.fin_tabs.f_name.get_text().strip()
        data.payload = {**data.payload, **self.fin_tabs.collect_payload()}
        data.gallery = self.fin_tabs.f_gallery_widget.get_data()
        return data.to_storage()

    # ----- Правка / сохранение / отмена (диспетчеры общих кнопок) -----

    def edit_current(self):
        if self._current_fin is not None:
            self.fin_toggle_edit()
        else:
            self.toggle_edit_mode()

    def save_current(self):
        if self._current_fin is not None:
            self.fin_save()
        else:
            self.save_account()

    def cancel_current(self):
        if self._current_fin is not None:
            self.fin_cancel()
        else:
            self.cancel_edit()

    def fin_toggle_edit(self):
        if self._card_busy:
            return
        self.is_editing = True
        self.fin_tabs.set_all_editable(True)
        self.edit_btn.hide(); self.save_btn.show(); self.cancel_btn.show()
        self._update_fin_status()

    def fin_save(self):
        if self._card_busy:
            return
        if not self._validate_card_name(self.fin_tabs, "финансовой записи"):
            return
        self._set_card_busy(True)
        util.fire(self._fin_save_async(self._card_gen))

    async def _fin_save_async(self, gen):
        session = self.db.current_session()
        fin = self._current_fin
        if fin is None:
            self._set_card_busy(False)
            return
        node_type, item_id = fin
        # Блокируем поля на время записи (снимок согласован до await), H65-01.
        active_tab = self.fin_tabs.currentIndex()
        self.fin_tabs.set_all_editable(False)
        # Дождаться незавершённых загрузок картинок галереи: иначе get_data()
        # пропустит ещё не дочитанные BLOB и Save «потеряет» изображение (M6-01).
        gallery = self.fin_tabs.f_gallery_widget
        if gallery.has_pending_uploads():
            self.statusBar().showMessage("Дождитесь загрузки изображений…", 2000)
            await gallery.wait_pending_uploads()
            if gen != self._card_gen or self._current_fin != fin:
                return
        storage = self._collect_fin_storage()
        link_ids = self.fin_tabs.f_linked_accounts.get_data()
        # Карточка и связи с аккаунтами — атомарно, одной транзакцией (§8).
        try:
            gallery_ids = await self.db.run_async(
                self.db.save_fin_item_with_links, item_id, storage, link_ids,
                _session=session)
        except StaleSessionError:
            # БД сменена во время записи (restore, вкл/выкл шифрования) — запись
            # не выполнена. Если карточка всё ещё открыта (gen совпал), её НЕЛЬЗЯ
            # оставлять busy/read-only: возвращаем черновик в кеш и разблокируем.
            if gen == self._card_gen:
                self._edit_cache[fin] = {"storage": storage, "links": link_ids}
                self._dirty_ids.add(fin)
                self._refresh_fin_marker(node_type, item_id)
                self.fin_tabs.set_all_editable(True)
                self.fin_tabs.setCurrentIndex(active_tab)
                self._set_card_busy(False)
                self.statusBar().showMessage(
                    "База была переоткрыта — сохранение не выполнено, "
                    "правки в черновике. Повторите сохранение.", 6000)
            return
        except Exception as e:                       # noqa: BLE001
            if gen == self._card_gen:
                # Снимок не теряем: возвращаем в кеш правок для повтора.
                self._edit_cache[fin] = {"storage": storage, "links": link_ids}
                self._dirty_ids.add(fin)
                self._refresh_fin_marker(node_type, item_id)
                self.fin_tabs.set_all_editable(True)
                self.fin_tabs.setCurrentIndex(active_tab)
                self._show_card_error("Не удалось сохранить запись", e)
                self._set_card_busy(False)
            return

        self._any_db_changes = True
        if gen == self._card_gen:
            # Новые картинки получают id своих строк — повторное сохранение
            # обновит их, а не пересоздаст (лишняя перезапись BLOB).
            gallery.assign_saved_ids(gallery_ids)
            self._edit_cache.pop(fin, None)
            self._dirty_ids.discard(fin)
            self.is_editing = False
            self.fin_tabs.set_all_editable(False)
            self.edit_btn.show(); self.save_btn.hide(); self.cancel_btn.hide()
            self._update_fin_banner(storage.get("item_type"),
                                    storage.get("payload") or {})
            # Пересинхронизируем спископодобные виджеты из сохранённого payload
            # (пустые строки уже отфильтрованы get_items) — тем же путём, что при
            # загрузке (см. _apply_fin_storage/load_payload): пустые строки
            # исчезают и из интерфейса.
            self._resync_fin_lists(storage.get("payload") or {})
            self._set_card_busy(False)
        # Обновляем узел дерева точечно (last4/срок/маркер правок).
        self._update_fin_item_after_save(node_type, item_id, storage)
        self.statusBar().showMessage("СОХРАНЕНО!", 2000)

    def fin_cancel(self):
        if self._card_busy:
            return
        self._set_card_busy(True)
        util.fire(self._fin_cancel_async(self._card_gen))

    async def _fin_cancel_async(self, gen):
        session = self.db.current_session()
        fin = self._current_fin
        if fin is None:
            self._set_card_busy(False)
            return
        node_type, item_id = fin
        self._edit_cache.pop(fin, None)
        self._dirty_ids.discard(fin)
        self.is_editing = False
        try:
            storage = await self.db.run_async(
                self.db.load_fin_item, item_id, _session=session)
            if gen != self._card_gen:
                return
            if storage is None:
                self._current_fin = None
                self._show_placeholder()
                self._update_fin_status()
                self._reload_tree()
                return
            links = await self.db.run_async(
                self.db.get_item_link_accounts, item_id, _session=session)
            if gen != self._card_gen:
                return
            self._apply_fin_storage(storage, links)
            self.fin_tabs.set_all_editable(False)
            self.edit_btn.show(); self.save_btn.hide(); self.cancel_btn.hide()
            self._update_fin_status()
            self._refresh_fin_marker(node_type, item_id)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            if gen == self._card_gen:
                self._show_card_error("Не удалось отменить правки", e)
        finally:
            if gen == self._card_gen:
                self._set_card_busy(False)

    # ----- Связи запись ↔ аккаунт (вкладка «Связи», §8) -----

    def on_add_fin_account_link_requested(self):
        """«+ СВЯЗАТЬ АККАУНТ» на карточке записи: выбор из живых аккаунтов.
        Чтение списка — в фоновом потоке БД (H-8), диалог — после загрузки."""
        if self._current_fin is None:
            return
        util.fire(self._add_fin_account_link_async())

    async def _add_fin_account_link_async(self):
        session = self.db.current_session()
        try:
            accounts = await self.db.run_async(
                self.db.get_all_accounts, _session=session)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            self._show_card_error("Не удалось загрузить список аккаунтов", e)
            return
        widget = self.fin_tabs.f_linked_accounts
        chosen, ok = theme.themed_multiselect(
            self.config, self, "Связать с аккаунтами", accounts,
            widget.get_data())
        if ok:
            chosen_set = set(chosen)
            links = [a for a in accounts if a["id"] in chosen_set]
            widget.set_data(links)
            widget.set_editable(self.is_editing)

    # ----- Осиротевшая загрузка галереи (§5, M7-05) -----

    def _on_fin_gallery_orphan_upload(self, fin_key, desc, data):
        """Импорт картинки стартовал для записи fin_key, но к завершению карточку
        сменили (виджет галереи общий на тип). Не теряем картинку — адресуем её
        своей записи: живой виджет (вернулись в правку), черновик правок либо
        новый черновик из БД (по образцу аккаунтной галереи)."""
        if fin_key is None:
            return
        if fin_key == self._current_fin and self.is_editing:
            self.fin_tabs.f_gallery_widget.add_item(data, desc)
            self._mark_fin_orphan_dirty(fin_key)
            return
        if fin_key in self._edit_cache:
            self._edit_cache[fin_key]["storage"].setdefault("gallery", []).append(
                {"desc": desc, "data": data, "image_id": None, "blob_size": None})
            self._mark_fin_orphan_dirty(fin_key)
            return
        util.fire(self._fin_orphan_into_new_draft(fin_key, desc, data))

    async def _fin_orphan_into_new_draft(self, fin_key, desc, data):
        """Создать черновик правок записи из состояния в БД и дописать в него
        осиротевшую картинку. Если запись удалена — сообщаем и роняем."""
        _node_type, item_id = fin_key
        session = self.db.current_session()
        try:
            storage = await self.db.run_async(
                self.db.load_fin_item, item_id, _session=session)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            logging.error("Не удалось подгрузить запись для осиротевшей "
                          "загрузки: %s", e, exc_info=e)
            return
        if storage is None:
            self.statusBar().showMessage("Загрузка отменена: запись удалена", 4000)
            return
        # За время await пользователь мог вернуться/занести черновик — не теряем.
        if fin_key == self._current_fin and self.is_editing:
            self.fin_tabs.f_gallery_widget.add_item(data, desc)
        elif fin_key in self._edit_cache:
            self._edit_cache[fin_key]["storage"].setdefault("gallery", []).append(
                {"desc": desc, "data": data, "image_id": None, "blob_size": None})
        else:
            fin_data = FinItemData.from_storage(storage)
            fin_data.gallery = list(fin_data.gallery) + [
                {"desc": desc, "data": data, "image_id": None, "blob_size": None}]
            try:
                link_ids = await self.db.run_async(
                    self.db.get_item_links, item_id, _session=session)
            except StaleSessionError:
                return
            except Exception as e:                    # noqa: BLE001
                # H-02 (симметрично server_card.py): НЕ подменяем неизвестные
                # связи пустым списком — иначе последующее сохранение этого
                # черновика стёрло бы реальные fin_links. None = «не трогать
                # при сохранении» (save_fin_item_with_links/_save_unsaved_
                # before_exit).
                logging.error("Не удалось подгрузить связи записи для "
                              "осиротевшей загрузки: %s", e, exc_info=e)
                link_ids = None
            self._edit_cache[fin_key] = {
                "storage": fin_data.to_storage(), "links": link_ids}
        self._mark_fin_orphan_dirty(fin_key)

    def _mark_fin_orphan_dirty(self, fin_key):
        """Пометить запись «грязной» и перерисовать её узел после осиротевшей
        загрузки."""
        self._dirty_ids.add(fin_key)
        self._refresh_fin_marker(*fin_key)

    # ----- Кеш несохранённых правок -----

    def _stash_current_fin_edits(self):
        """Сохраняет несохранённые правки записи в память (не в БД)."""
        if self._current_fin is None:
            return
        storage = self._collect_fin_storage()
        # links — id аккаунтов с вкладки «Связи» (формат общий с аккаунтом).
        self._edit_cache[self._current_fin] = {
            "storage": storage,
            "links": self.fin_tabs.f_linked_accounts.get_data()}
        self._dirty_ids.add(self._current_fin)

    # ----- Плашка/статус/дерево -----

    def _update_fin_banner(self, item_type, payload):
        """Плашка над вкладками: «Истекает через N дн. / истекла».

        Срок берётся из spec.extract(payload) — того же экстракта, что пишет
        expires_on в БД и питает маркер [!] в дереве, а не из захардкоженного
        ключа 'expiry' — иначе банер знал бы только про карты и расходился бы
        с деревом на любом другом типе со сроком действия."""
        spec = FIN_TYPES.get(item_type)
        days = None
        if spec is not None:
            _last4, expires_on = spec.extract(payload)
            days = days_until_expiry(expires_on)
        if days is None:
            self.fin_banner.hide()
            return
        if days < 0:
            self.fin_banner.setText("[!] Истекла")
        elif days <= EXPIRY_WARN_DAYS:
            self.fin_banner.setText(f"[!] Истекает через {days} дн.")
        else:
            self.fin_banner.hide()
            return
        self.fin_banner.show()

    def _update_fin_status(self):
        """Индикатор в статус-баре: режим + имя записи."""
        if self._current_fin is None:
            return
        prefix = "[РЕД] " if self.is_editing else "[ПРОСМОТР] "
        name = self.current_fin_data.name if self.current_fin_data else ""
        self.status_info.setText(prefix + name)

    def _refresh_fin_marker(self, node_type, item_id):
        """Точечно перерисовать узел записи (маркер несохранённых правок/срока)."""
        item = self._find_leaf_item(node_type, item_id)
        if item is not None:
            self._apply_item_style(item, self._node(item))

    def _update_fin_item_after_save(self, node_type, item_id, storage):
        """Обновить узел записи после сохранения без полной перестройки дерева:
        имя, экстракт-значения (last4/срок) и снятие маркера правок. Полная
        перестройка — лишь если сменившееся имя меняет ПОРЯДОК (сортировка по
        имени) либо узел не найден (fallback)."""
        item = self._find_leaf_item(node_type, item_id)
        if item is None:
            self._reload_tree()
            return
        node = self._node(item)
        new_name = storage.get("name") or node["name"]
        name_changed = node["name"] != new_name
        spec = FIN_TYPES.get(storage.get("item_type"))
        last4, days = None, None
        if spec is not None:
            l4, expires_on = spec.extract(storage.get("payload") or {})
            last4 = l4 or None
            days = days_until_expiry(expires_on)
        node.update({"name": new_name, "card_last4": last4,
                     "days_until_expiry": days})
        item.setData(0, Qt.UserRole, node)
        item.setToolTip(0, new_name)
        self._apply_item_style(item, node)
        if self.sort_mode == "name" and name_changed:
            self._reload_tree()                  # порядок по имени мог измениться
        elif self.search_text:
            self._apply_filter()                 # видимость по имени/last4 могла измениться
