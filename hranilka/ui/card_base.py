"""Общий поток карточек правой панели (аккаунт / фин-запись / сервер).

Раньше save/cancel/load/stash/orphan-загрузка галереи были тремя почти
одинаковыми копиями (account_card.py, fin_card.py, server_card.py). Здесь —
общая часть: `CardFlowMixin` + `LeafCardSpec` (описание «листовой» карточки:
имена атрибутов окна, методов БД и хуков). Поведение не менялось: методы
резолвятся по имени в момент вызова (getattr), поэтому подмена метода БД/окна
в тестах работает как раньше.

Чистые функции (`orphan_gallery_entry`, `append_orphan_to_draft`,
`edit_flag_stale`) — без Qt и состояния, тестируются отдельно.
"""
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from hranilka.ui import sounds
from hranilka.data.database import StaleSessionError
from hranilka.core import util

Key = tuple[str, int]


# ----- Чистая логика -----

def orphan_gallery_entry(desc: Any, data: Any) -> dict:
    """Элемент галереи для осиротевшей загрузки (формат черновика правок)."""
    return {"desc": desc, "data": data, "image_id": None, "blob_size": None}


def append_orphan_to_draft(draft: dict, desc: Any, data: Any) -> None:
    """Дописать осиротевшую картинку в галерею storage черновика правок.
    setdefault: у старых черновиков ключа gallery может не быть."""
    draft["storage"].setdefault("gallery", []).append(
        orphan_gallery_entry(desc, data))


def edit_flag_stale(flag: Any, value: Any) -> bool:
    """Одноразовый флаг «открыть в правке» устарел: взведён, но для другой
    записи (value) — его надо погасить."""
    return flag is not None and flag != value


# ----- Описание листовой карточки -----

@dataclass(frozen=True)
class LeafCardSpec:
    """Адаптер листовой карточки (фин-запись/сервер) для общего потока.

    Строковые поля — имена атрибутов окна/БД (резолвятся в момент вызова).
    Callable-хуки принимают окно первым аргументом."""
    tabs_attr: str            # "fin_tabs" / "server_tabs"
    key_attr: str             # "_current_fin" / "_current_server"
    edit_flag_attr: str       # "_fin_edit_on_load" / "_server_edit_on_load"
    edit_flag_value: Callable[[Key], Any]    # key -> значение флага
    data_attr: str            # "current_fin_data" / "current_server_data"
    data_cls: Any             # FinItemData / ServerData (from_storage/to_storage)
    entity_name: str          # для проверки названия: «сервера»
    # --- методы БД ---
    db_load: str              # load_fin_item / get_server
    db_link_accounts: str     # get_item_link_accounts / get_server_link_accounts
    db_links: str             # get_item_links / get_server_links (H-02 перечитать)
    db_save: str              # save_fin_item_with_links / save_server_with_links
    exclude_kw: str           # exclude_fin_item_id / exclude_server_id
    # --- методы окна ---
    apply: str                # _apply_fin_storage / _apply_server_storage
    collect: str              # _collect_fin_storage / _collect_server_storage
    status: str               # _update_fin_status / _update_server_status
    marker: Callable[[Any, Key], None]               # перерисовать узел
    on_saved: Callable[[Any, dict], None]            # после успешного save (UI)
    update_item_after_save: Callable[[Any, Key, dict], None]   # узел дерева
    # --- тексты ---
    err_load: str
    err_save: str
    err_recover: str          # H-02: связи черновика не восстановлены
    orphan_log_load: str
    orphan_deleted: str       # «Загрузка отменена: …»
    # --- необязательное ---
    after_load: Optional[Callable[[Any], None]] = None
    fail_closed_excs: tuple = ()
    fail_closed_msg: str = ""
    # Хук ссылок для нового черновика осиротевшей загрузки: async (win, key,
    # storage, session) -> link_ids | None. StaleSessionError пробрасывать.
    orphan_links: Optional[Callable[..., Any]] = None


class CardFlowMixin:
    """Общий поток карточек. Подмешивается в Account/Fin/ServerCardMixin."""

    # ----- UI режима -----

    def _set_edit_mode_ui(self, editing: bool) -> None:
        """Кнопки правой панели: правка — Save/Cancel, просмотр — Edit."""
        if editing:
            self.edit_btn.hide()
            self.save_btn.show()
            self.cancel_btn.show()
        else:
            self.edit_btn.show()
            self.save_btn.hide()
            self.cancel_btn.hide()

    def _drop_stale_edit_flag(self, attr: str, value: Any) -> None:
        """Погасить одноразовый флаг «открыть в правке» для чужой записи."""
        if edit_flag_stale(getattr(self, attr), value):
            setattr(self, attr, None)

    # ----- Листовые карточки: правка / кнопки -----

    def _leaf_toggle_edit(self, spec: LeafCardSpec) -> None:
        if self._card_busy:
            return
        self.is_editing = True
        getattr(self, spec.tabs_attr).set_all_editable(True)
        self._set_edit_mode_ui(True)
        getattr(self, spec.status)()

    def _leaf_save(self, spec: LeafCardSpec,
                   coro_factory: Callable[[int], Any]) -> None:
        if self._card_busy:
            return
        if not self._validate_card_name(getattr(self, spec.tabs_attr),
                                        spec.entity_name):
            return
        self._set_card_busy(True)
        util.fire(coro_factory(self._card_gen))

    # ----- Stash -----

    def _leaf_stash(self, spec: LeafCardSpec) -> None:
        key = getattr(self, spec.key_attr)
        if key is None:
            return
        storage = getattr(self, spec.collect)()
        self._edit_cache[key] = {
            "storage": storage,
            "links": getattr(self, spec.tabs_attr).f_linked_accounts.get_data()}
        self._dirty_ids.add(key)

    def _leaf_mark_dirty(self, spec: LeafCardSpec, key: Key) -> None:
        self._dirty_ids.add(key)
        spec.marker(self, key)

    # ----- Загрузка -----

    async def _leaf_load_async(self, spec: LeafCardSpec, key: Key, gen: int) -> None:
        """Загрузить карточку (или восстановить из кеша правок). После каждого
        await сверяем gen: устаревшая загрузка выходит, не трогая UI."""
        session = self.db.current_session()
        item_id = key[1]
        tabs = getattr(self, spec.tabs_attr)
        try:
            if key in self._edit_cache:
                cached = self._edit_cache[key]
                link_ids = cached.get("links")
                if link_ids is None:
                    # H-02: связи черновика неизвестны — НЕ подменяем []:
                    # сохранение стёрло бы реальные привязки. Перечитываем;
                    # вторая неудача — fail-closed, черновик не трогаем.
                    try:
                        link_ids = await self.db.run_async(
                            getattr(self.db, spec.db_links), item_id,
                            _session=session)
                    except StaleSessionError:
                        return
                    except Exception as e:            # noqa: BLE001
                        if gen == self._card_gen:
                            setattr(self, spec.key_attr, None)
                            self.is_editing = False
                            self._show_placeholder()
                            self._show_card_error(spec.err_recover, e)
                        return
                    if gen != self._card_gen:
                        return
                    cached["links"] = link_ids        # связи стали известны
                links = await self.db.run_async(
                    self._resolve_link_names, link_ids, _session=session)
                other_bytes = await self.db.run_async(
                    self.db.gallery_total_bytes, _session=session,
                    **{spec.exclude_kw: item_id})
                if gen != self._card_gen:
                    return
                getattr(self, spec.apply)(cached["storage"], links, other_bytes)
                self.is_editing = True
                tabs.set_all_editable(True)
                self._set_edit_mode_ui(True)
            else:
                storage = await self.db.run_async(
                    getattr(self.db, spec.db_load), item_id, _session=session)
                if gen != self._card_gen:
                    return
                if storage is None:
                    # Запись исчезла между выбором и загрузкой — заглушка.
                    setattr(self, spec.key_attr, None)
                    self.is_editing = False
                    self._show_placeholder()
                    getattr(self, spec.status)()
                    return
                links = await self.db.run_async(
                    getattr(self.db, spec.db_link_accounts), item_id,
                    _session=session)
                other_bytes = await self.db.run_async(
                    self.db.gallery_total_bytes, _session=session,
                    **{spec.exclude_kw: item_id})
                if gen != self._card_gen:
                    return
                getattr(self, spec.apply)(storage, links, other_bytes)
                self.is_editing = False
                tabs.set_all_editable(False)
                self._set_edit_mode_ui(False)
                if getattr(self, spec.edit_flag_attr) == spec.edit_flag_value(key):
                    # Только что созданная запись — сразу в режим правки.
                    setattr(self, spec.edit_flag_attr, None)
                    self.is_editing = True
                    tabs.set_all_editable(True)
                    self._set_edit_mode_ui(True)
            getattr(self, spec.status)()
            if spec.after_load is not None:
                spec.after_load(self)
        except StaleSessionError:
            return                               # БД сменена (lock/restore) — молча
        except spec.fail_closed_excs as e:
            # M-03: payload повреждён — fail-closed: карточку не открываем,
            # строка в БД остаётся нетронутой.
            if gen == self._card_gen:
                setattr(self, spec.key_attr, None)
                self.is_editing = False
                self._show_placeholder()
                self._show_card_error(spec.fail_closed_msg, e)
        except Exception as e:                       # noqa: BLE001
            if gen == self._card_gen:
                self._show_card_error(spec.err_load, e)
        finally:
            if gen == self._card_gen:
                self._set_card_busy(False)

    # ----- Сохранение -----

    async def _leaf_save_async(self, spec: LeafCardSpec, gen: int) -> None:
        session = self.db.current_session()
        key = getattr(self, spec.key_attr)
        if key is None:
            self._set_card_busy(False)
            return
        item_id = key[1]
        tabs = getattr(self, spec.tabs_attr)
        # Блокируем поля на время записи (снимок согласован до await), H65-01.
        active_tab = tabs.currentIndex()
        tabs.set_all_editable(False)
        # Дождаться незавершённых загрузок картинок: иначе get_data() пропустит
        # ещё не дочитанные BLOB и Save «потеряет» изображение (M6-01).
        gallery = tabs.f_gallery_widget
        if gallery.has_pending_uploads():
            self.statusBar().showMessage("Дождитесь загрузки изображений…", 2000)
            await gallery.wait_pending_uploads()
            if gen != self._card_gen or getattr(self, spec.key_attr) != key:
                return
        storage = getattr(self, spec.collect)()
        link_ids = tabs.f_linked_accounts.get_data()
        # Карточка и связи с аккаунтами — атомарно, одной транзакцией.
        try:
            gallery_ids = await self.db.run_async(
                getattr(self.db, spec.db_save), item_id, storage, link_ids,
                _session=session)
        except StaleSessionError:
            # БД сменена во время записи (restore, вкл/выкл шифрования) — запись
            # не выполнена. Карточку нельзя оставлять busy/read-only: возвращаем
            # черновик в кеш и разблокируем.
            if gen == self._card_gen:
                self._restore_failed_save(spec, key, storage, link_ids,
                                          tabs, active_tab)
                self._set_card_busy(False)
                self.statusBar().showMessage(
                    "База была переоткрыта — сохранение не выполнено, "
                    "правки в черновике. Повторите сохранение.", 6000)
            return
        except Exception as e:                       # noqa: BLE001
            if gen == self._card_gen:
                # Снимок не теряем: возвращаем в кеш правок для повтора.
                self._restore_failed_save(spec, key, storage, link_ids,
                                          tabs, active_tab)
                self._show_card_error(spec.err_save, e)
                self._set_card_busy(False)
            return

        self._any_db_changes = True
        if gen == self._card_gen:
            # Новые картинки получают id своих строк — повторное сохранение
            # обновит их, а не пересоздаст (лишняя перезапись BLOB).
            gallery.assign_saved_ids(gallery_ids)
            self._edit_cache.pop(key, None)
            self._dirty_ids.discard(key)
            self.is_editing = False
            tabs.set_all_editable(False)
            self._set_edit_mode_ui(False)
            spec.on_saved(self, storage)
            self._set_card_busy(False)
        # Узел дерева — точечно (имя/экстракты/маркер правок).
        spec.update_item_after_save(self, key, storage)
        self.statusBar().showMessage("СОХРАНЕНО!", 2000)
        sounds.play(getattr(self, "config", None), "save")

    def _restore_failed_save(self, spec: LeafCardSpec, key: Key, storage: dict,
                             link_ids: Any, tabs: Any, active_tab: int) -> None:
        """Неудачное сохранение: снимок обратно в кеш правок, поля снова
        editable, вернуть активную вкладку."""
        self._edit_cache[key] = {"storage": storage, "links": link_ids}
        self._dirty_ids.add(key)
        spec.marker(self, key)
        tabs.set_all_editable(True)
        tabs.setCurrentIndex(active_tab)

    # ----- Отмена -----

    def _leaf_cancel(self, spec: LeafCardSpec, coro_factory: Callable[[int], Any]) -> None:
        if self._card_busy:
            return
        self._set_card_busy(True)
        util.fire(coro_factory(self._card_gen))

    async def _leaf_cancel_async(self, spec: LeafCardSpec, gen: int) -> None:
        session = self.db.current_session()
        key = getattr(self, spec.key_attr)
        if key is None:
            self._set_card_busy(False)
            return
        item_id = key[1]
        self._edit_cache.pop(key, None)
        self._dirty_ids.discard(key)
        self.is_editing = False
        try:
            storage = await self.db.run_async(
                getattr(self.db, spec.db_load), item_id, _session=session)
            if gen != self._card_gen:
                return
            if storage is None:
                setattr(self, spec.key_attr, None)
                self._show_placeholder()
                getattr(self, spec.status)()
                self._reload_tree()
                return
            links = await self.db.run_async(
                getattr(self.db, spec.db_link_accounts), item_id,
                _session=session)
            if gen != self._card_gen:
                return
            getattr(self, spec.apply)(storage, links)
            getattr(self, spec.tabs_attr).set_all_editable(False)
            self._set_edit_mode_ui(False)
            getattr(self, spec.status)()
            spec.marker(self, key)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            if gen == self._card_gen:
                self._show_card_error("Не удалось отменить правки", e)
        finally:
            if gen == self._card_gen:
                self._set_card_busy(False)

    # ----- Осиротевшая загрузка галереи (M7-05) -----

    def _leaf_orphan_upload(self, spec: LeafCardSpec, key: Optional[Key],
                            desc: Any, data: Any, new_draft: Callable[..., Any]) -> None:
        """Импорт стартовал для key, но к завершению карточку сменили. Не теряем
        картинку: живой виджет (вернулись в правку) / черновик правок / новый
        черновик из БД (new_draft — корутина-фабрика)."""
        if key is None:
            return
        if key == getattr(self, spec.key_attr) and self.is_editing:
            getattr(self, spec.tabs_attr).f_gallery_widget.add_item(data, desc)
            self._leaf_mark_dirty(spec, key)
            return
        if key in self._edit_cache:
            append_orphan_to_draft(self._edit_cache[key], desc, data)
            self._leaf_mark_dirty(spec, key)
            return
        util.fire(new_draft(key, desc, data))

    async def _leaf_orphan_into_new_draft(self, spec: LeafCardSpec, key: Key,
                                          desc: Any, data: Any) -> None:
        """Создать черновик правок из состояния в БД и дописать в него
        осиротевшую картинку. Если запись удалена — сообщаем и роняем."""
        item_id = key[1]
        session = self.db.current_session()
        try:
            storage = await self.db.run_async(
                getattr(self.db, spec.db_load), item_id, _session=session)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            logging.error(spec.orphan_log_load + ": %s", e, exc_info=e)
            return
        if storage is None:
            self.statusBar().showMessage(spec.orphan_deleted, 4000)
            return
        # За время await пользователь мог вернуться/занести черновик — не теряем.
        if key == getattr(self, spec.key_attr) and self.is_editing:
            getattr(self, spec.tabs_attr).f_gallery_widget.add_item(data, desc)
        elif key in self._edit_cache:
            append_orphan_to_draft(self._edit_cache[key], desc, data)
        else:
            obj = spec.data_cls.from_storage(storage)
            obj.gallery = list(obj.gallery) + [orphan_gallery_entry(desc, data)]
            try:
                link_ids = await spec.orphan_links(self, key, storage, session)
            except StaleSessionError:
                return
            self._edit_cache[key] = {
                "storage": obj.to_storage(), "links": link_ids}
        self._leaf_mark_dirty(spec, key)
