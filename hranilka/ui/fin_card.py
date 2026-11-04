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
from hranilka.ui.card_base import CardFlowMixin, LeafCardSpec
from hranilka.ui import theme
from hranilka.core import util


def _fin_on_saved(win, storage):
    """После успешного сохранения: плашка срока + пересинхронизация списков
    из сохранённого payload (пустые строки уже отфильтрованы get_items)."""
    win._update_fin_banner(storage.get("item_type"), storage.get("payload") or {})
    win._resync_fin_lists(storage.get("payload") or {})


async def _fin_orphan_links(win, key, storage, session):
    """Связи для нового черновика осиротевшей загрузки. H-02: при сбое чтения
    возвращаем None («не трогать при сохранении»), а НЕ [] — иначе
    последующее сохранение стёрло бы реальные fin_links."""
    try:
        return await win.db.run_async(
            win.db.get_item_links, key[1], _session=session)
    except StaleSessionError:
        raise
    except Exception as e:                    # noqa: BLE001
        logging.error("Не удалось подгрузить связи записи для "
                      "осиротевшей загрузки: %s", e, exc_info=e)
        return None


_FIN_SPEC = LeafCardSpec(
    tabs_attr="fin_tabs", key_attr="_current_fin",
    edit_flag_attr="_fin_edit_on_load", edit_flag_value=lambda k: k,
    data_attr="current_fin_data", data_cls=FinItemData,
    entity_name="финансовой записи",
    db_load="load_fin_item", db_link_accounts="get_item_link_accounts",
    db_links="get_item_links", db_save="save_fin_item_with_links",
    exclude_kw="exclude_fin_item_id",
    apply="_apply_fin_storage", collect="_collect_fin_storage",
    status="_update_fin_status",
    marker=lambda w, k: w._refresh_fin_marker(*k),
    on_saved=_fin_on_saved,
    update_item_after_save=lambda w, k, st: w._update_fin_item_after_save(
        k[0], k[1], st),
    err_load="Не удалось загрузить запись",
    err_save="Не удалось сохранить запись",
    err_recover=("Не удалось восстановить связи черновика записи — открытие "
                 "отменено, чтобы не потерять привязки"),
    orphan_log_load="Не удалось подгрузить запись для осиротевшей загрузки",
    orphan_deleted="Загрузка отменена: запись удалена",
    orphan_links=_fin_orphan_links,
)


class FinCardMixin(CardFlowMixin):
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
        self._drop_stale_edit_flag("_fin_edit_on_load", (node_type, item_id))
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
        """Загрузить карточку записи (или восстановить из кеша правок)."""
        await self._leaf_load_async(_FIN_SPEC, (node_type, item_id), gen)

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
        self._leaf_toggle_edit(_FIN_SPEC)

    def fin_save(self):
        self._leaf_save(_FIN_SPEC, self._fin_save_async)

    async def _fin_save_async(self, gen):
        await self._leaf_save_async(_FIN_SPEC, gen)

    def fin_cancel(self):
        self._leaf_cancel(_FIN_SPEC, self._fin_cancel_async)

    async def _fin_cancel_async(self, gen):
        await self._leaf_cancel_async(_FIN_SPEC, gen)

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
        """Импорт картинки стартовал для записи fin_key, но карточку сменили
        (виджет галереи общий на тип) — адресуем картинку своей записи."""
        self._leaf_orphan_upload(_FIN_SPEC, fin_key, desc, data,
                                 self._fin_orphan_into_new_draft)

    async def _fin_orphan_into_new_draft(self, fin_key, desc, data):
        await self._leaf_orphan_into_new_draft(_FIN_SPEC, fin_key, desc, data)

    def _mark_fin_orphan_dirty(self, fin_key):
        """Пометить запись «грязной» и перерисовать её узел."""
        self._leaf_mark_dirty(_FIN_SPEC, fin_key)

    # ----- Кеш несохранённых правок -----

    def _stash_current_fin_edits(self):
        """Сохраняет несохранённые правки записи в память (не в БД)."""
        self._leaf_stash(_FIN_SPEC)

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
