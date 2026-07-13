"""Карточка аккаунта главного окна (Эпик 4.4).

Примесь (mixin) с логикой правой панели: загрузка/сохранение карточки, режим
редактирования, кеш несохранённых правок, связанные аккаунты, генерация пароля
и ПД, обновление строки статуса. Состояние разделяется с окном через self —
поведение идентично прежнему. Подмешивается в MainWindow перед QMainWindow."""
import logging

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QDialog

from hranilka.core.nodetypes import ACCOUNT
from hranilka.data.database import StaleSessionError
from hranilka.data.models import AccountData
from hranilka.ui.generator_dialog import GeneratorSettingsDialog
from hranilka.ui.widgets import fin_item_display
from hranilka.generators import password_gen
from hranilka.generators import pd_generator
from hranilka.core import util
from hranilka.core import domain
from hranilka.ui import theme


class AccountCardMixin:
    """Правая панель: просмотр/редактирование карточки, связи, генерация, статус."""

    def _fin_enabled(self):
        """Показывать ли фин-инструменты (карты/кошельки) в интерфейсе."""
        return self.config.get("show_fin_instruments", False)

    def _current_fin_link_ids(self):
        """id привязанных фин-записей карточки для сохранения/стеша. При
        выключенной опции возвращает None: скрытый пустой виджет НЕ должен
        стереть реальные связи fin_links (None не трогает их в БД)."""
        return self.tabs.f_fin_linked.get_data() if self._fin_enabled() else None

    def _show_placeholder(self):
        # Правая панель — QStackedWidget (заглушка / аккаунт / фин-запись);
        # переключение страниц вместо show/hide отдельных виджетов.
        self.right_stack.setCurrentWidget(self.placeholder_label)
        self.fin_banner.hide()
        self.edit_btn.hide()
        self.save_btn.hide()
        self.cancel_btn.hide()

    def _set_card_busy(self, busy):
        """Блокирует кнопки Edit/Save/Cancel на время async-загрузки/сохранения
        карточки. Пока идёт загрузка нового аккаунта, форма ещё показывает данные
        предыдущего — без блокировки пользователь мог бы нажать «Редактировать» и
        записать чужой снимок (H6-01); блокировка Save исключает и повторный
        запуск сохранения (H6-02)."""
        self._card_busy = busy
        self.edit_btn.setEnabled(not busy)
        self.save_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(not busy)

    def _on_gallery_upload_status(self, loading):
        """Индикация в статус-баре, пока идёт async-загрузка картинки в галерею
        (файл с диска или из буфера) — до этого момента BLOB ещё не в памяти,
        и пользователь не видит, что что-то происходит в фоне."""
        if loading:
            self.statusBar().showMessage("[ ЗАГРУЗКА ]")   # без таймаута — до конца
        else:
            self.statusBar().clearMessage()

    def _on_gallery_orphan_upload(self, account_id, desc, data):
        """Осиротевшая загрузка галереи (M7-05): импорт стартовал для account_id,
        но к моменту завершения пользователь ушёл с карточки. Не теряем картинку —
        адресуем её своему аккаунту.

          * пользователь ВЕРНУЛСЯ (account_id == текущий, карточка открыта) →
            добавляем прямо в живой виджет и помечаем аккаунт «грязным»;
          * иначе → дописываем в черновик правок account_id (или подгружаем его из
            БД, если черновика ещё нет) и помечаем «грязным».
        Если аккаунт удалён — сообщаем и роняем."""
        if account_id is None:
            return
        if (account_id == self._current_account_id
                and self._current_account_id is not None and self.is_editing):
            # Вернулись на карточку в режиме правки: кладём в живой виджет как
            # ручную правку (при следующем switch попадёт в _stash_current_edits).
            self.tabs.f_gallery_widget.add_item(data, desc)
            self._dirty_ids.add((ACCOUNT, account_id))
            self._refresh_dirty_markers(account_id)
            return
        # Ушли на другую карточку/заглушку (или карточка не в правке) — картинку
        # кладём в черновик правок, чтобы она не потерялась вне режима правки.
        if (ACCOUNT, account_id) in self._edit_cache:
            self._edit_cache[(ACCOUNT, account_id)]["storage"]["gallery"].append(
                {"desc": desc, "data": data, "image_id": None, "blob_size": None})
            self._dirty_ids.add((ACCOUNT, account_id))
            self._refresh_dirty_markers(account_id)
            return
        # Черновика ещё нет (edge: пользователь не редактировал этот аккаунт —
        # кнопки загрузки видны только в правке, но карточку могли сменить сразу).
        # Подгружаем аккаунт из БД, формируем черновик в формате _stash_current_edits.
        util.fire(self._orphan_into_new_draft(account_id, desc, data))

    async def _orphan_into_new_draft(self, account_id, desc, data):
        """Создать черновик правок для account_id из состояния в БД и дописать в
        него осиротевшую картинку (M7-05). Если аккаунт удалён — сообщаем и роняем."""
        session = self.db.current_session()
        try:
            storage = await self.db.run_async(
                self.db.load_account, account_id, _session=session)
        except StaleSessionError:
            return                               # сессия сменилась (lock/restore) — молча
        except Exception as e:                   # noqa: BLE001
            logging.error("Не удалось подгрузить аккаунт для осиротевшей "
                          "загрузки: %s", e, exc_info=e)
            return
        if storage is None:
            # Аккаунт удалён, пока грузилась картинка — сохранять некуда.
            self.statusBar().showMessage("Загрузка отменена: аккаунт удалён", 4000)
            return
        # Могло случиться, что за время await пользователь уже вернулся на карточку
        # в режиме правки — тогда кладём в живой виджет, не перетирая состояние.
        if account_id == self._current_account_id and self.is_editing:
            self.tabs.f_gallery_widget.add_item(data, desc)
            self._dirty_ids.add((ACCOUNT, account_id))
            self._refresh_dirty_markers(account_id)
            return
        if (ACCOUNT, account_id) in self._edit_cache:
            self._edit_cache[(ACCOUNT, account_id)]["storage"]["gallery"].append(
                {"desc": desc, "data": data, "image_id": None, "blob_size": None})
        else:
            # Формат черновика идентичен _stash_current_edits: storage из
            # AccountData + links (id связанных аккаунтов).
            acc = AccountData.from_storage(storage)
            acc.gallery = list(acc.gallery)
            acc.gallery.append(
                {"desc": desc, "data": data, "image_id": None, "blob_size": None})
            # В черновике links — список id (формат _stash_current_edits, который
            # берёт f_linked.get_data()); db.get_links отдаёт dict'ы — берём id.
            try:
                link_rows = await self.db.run_async(
                    self.db.get_links, account_id, _session=session)
            except StaleSessionError:
                return
            except Exception as e:               # noqa: BLE001
                logging.error("Не удалось подгрузить связи для осиротевшей "
                              "загрузки: %s", e, exc_info=e)
                link_rows = []
            link_ids = [r["id"] for r in link_rows]
            # Привязанные фин-записи — тоже в черновик: иначе сохранение из
            # такого черновика перезаписало бы связи fin_links пустым списком.
            # При выключенной опции — None (сохранение не тронет fin_links).
            fin_link_ids = None
            if self._fin_enabled():
                try:
                    fin_rows = await self.db.run_async(
                        self.db.get_account_fin_links, account_id,
                        _session=session)
                except StaleSessionError:
                    return
                except Exception as e:           # noqa: BLE001
                    logging.error("Не удалось подгрузить фин-связи для "
                                  "осиротевшей загрузки: %s", e, exc_info=e)
                    fin_rows = []
                fin_link_ids = [r["id"] for r in fin_rows]
            self._edit_cache[(ACCOUNT, account_id)] = {
                "storage": acc.to_storage(), "links": link_ids,
                "fin_links": fin_link_ids}
        self._dirty_ids.add((ACCOUNT, account_id))
        self._refresh_dirty_markers(account_id)

    def _quiesce_card_async(self):
        """Погасить весь незавершённый async карточки и галереи ПЕРЕД сменой сессии
        БД (lock/restore/close). Новое поколение карточки делает устаревшими висящие
        coroutine загрузки/сохранения: их проверки gen после await прерывают работу,
        а finally не трогает UI уже другой сессии. Галерея отменяет импорт и
        предпросмотр. Вызывать ДО db.lock()/close()/restore (H65-02)."""
        self._card_gen += 1
        self._card_busy = False
        try:
            self.tabs.f_gallery_widget.cancel_all_tasks()
            # Галереи фин-карточек (по одной на тип реестра) — тоже гасим.
            for fin_tabs in self.fin_tabs_by_type.values():
                fin_tabs.f_gallery_widget.cancel_all_tasks()
        except Exception as e:                       # noqa: BLE001 — teardown-хардненинг
            logging.warning("Не удалось отменить задачи галереи: %s", e)

    def _show_card_error(self, title, exc):
        """Показать пользователю ошибку async-операции карточки (L6-03): иначе
        кнопка визуально ничего не делает, а причина уходит только в лог.

        Пользователю — короткое сообщение без сырого текста исключения (M-14):
        в нём могут быть пути/внутренности БД. Полные детали — в лог (exc_info)."""
        logging.error("%s: %s", title, exc, exc_info=exc)
        theme.themed_info(
            self.config, self, title,
            f"Операция не выполнена ({type(exc).__name__}).\n"
            "Подробности — в логе программы.")

    def on_item_selected(self, current, previous):
        node = self._node(current)
        new_id = node["id"] if (node and node["type"] == ACCOUNT) else None

        # Одноразовый флаг «открыть в правке» действует только для своего
        # аккаунта: ушли на другой узел — гасим, чтобы правка не включилась
        # позже на чужой карточке.
        if self._edit_on_load_id is not None and self._edit_on_load_id != new_id:
            self._edit_on_load_id = None

        # Уходим с аккаунта, который сейчас редактируется → стэшим правки (не теряем их)
        if (self.is_editing and self._current_account_id is not None
                and self._current_account_id != new_id):
            self._stash_current_edits(self._current_account_id)
            self._refresh_dirty_markers(self._current_account_id)

        # Новое поколение карточки: любой ещё не завершённый async-результат для
        # прежнего аккаунта теперь устарел и не должен трогать UI/кеш.
        self._card_gen += 1
        gen = self._card_gen

        if new_id is None:
            self._current_account_id = None
            self.is_editing = False
            self._set_card_busy(False)
            self._show_placeholder()
            self._update_status_info()
            return

        self.right_stack.setCurrentWidget(self.tabs)
        self.fin_banner.hide()
        self.current_tree_item = current
        self._current_account_id = new_id
        # Чтение карточки/связей/объёма галереи — в фоновом потоке БД (UI не виснет).
        self._set_card_busy(True)
        util.fire(self._load_account_into_ui(new_id, gen))

    async def _load_account_into_ui(self, new_id, gen):
        """Асинхронно загрузить карточку и заполнить интерфейс. Все обращения к БД
        идут через db.run_async (фоновый поток). После каждого await сверяем gen с
        текущим: если пользователь переключился — результат устарел, выходим, не
        снимая блокировку (ею владеет более новая загрузка).

        Токен сессии (session) снимается ОДИН раз и передаётся в каждый run_async:
        если между await произошёл lock/restore/close, следующий вызов поднимет
        StaleSessionError и вся загрузка прервётся, не смешивая данные разных
        сессий vault (H65-02)."""
        session = self.db.current_session()
        try:
            if (ACCOUNT, new_id) in self._edit_cache:
                # Возврат к аккаунту с несохранёнными правками — восстанавливаем из кеша.
                cached = self._edit_cache[(ACCOUNT, new_id)]
                links = await self.db.run_async(
                    self._resolve_link_names, cached["links"], _session=session)
                # fin_links в черновике может быть None (стеш при выключенной
                # опции); при выключенной опции фин-связи не читаем вовсе.
                fin_links = []
                if self._fin_enabled():
                    fin_links = await self.db.run_async(
                        self._resolve_fin_link_rows,
                        cached.get("fin_links") or [], _session=session)
                other_bytes = await self.db.run_async(
                    self.db.gallery_total_bytes, new_id, _session=session)
                if gen != self._card_gen:
                    return
                self.current_account_data = AccountData.from_storage(cached["storage"])
                self.load_data_to_ui(links=links, other_bytes=other_bytes,
                                     fin_links=fin_links)
                self.is_editing = True
                self.tabs.set_all_editable(True)
                self.edit_btn.hide(); self.save_btn.show(); self.cancel_btn.show()
            else:
                storage = await self.db.run_async(
                    self.db.load_account, new_id, _session=session)
                if gen != self._card_gen:
                    return
                if storage is None:
                    # Аккаунт исчез между выбором и загрузкой (удалён/перемещён в
                    # корзину) — не падаем на from_storage(None), показываем заглушку.
                    self._current_account_id = None
                    self.is_editing = False
                    self._show_placeholder()
                    self._update_status_info()
                    return
                links = await self.db.run_async(
                    self.db.get_links, new_id, _session=session)
                # Фин-связи не читаем при выключенной опции (секция скрыта).
                fin_links = []
                if self._fin_enabled():
                    fin_links = await self.db.run_async(
                        self.db.get_account_fin_links, new_id, _session=session)
                other_bytes = await self.db.run_async(
                    self.db.gallery_total_bytes, new_id, _session=session)
                if gen != self._card_gen:
                    return
                self.current_account_data = AccountData.from_storage(storage)
                self.is_editing = False
                self.load_data_to_ui(links=links, other_bytes=other_bytes,
                                     fin_links=fin_links)
                self.tabs.set_all_editable(False)
                self.edit_btn.show(); self.save_btn.hide(); self.cancel_btn.hide()
                if self._edit_on_load_id == new_id:
                    # Только что созданный аккаунт — сразу в режим правки.
                    # Карточка уже в БД; это UI-переключение ПОСЛЕ полной
                    # загрузки (gen сверен выше), гонок с async нет.
                    self._edit_on_load_id = None
                    self.is_editing = True
                    self.tabs.set_all_editable(True)
                    self.edit_btn.hide(); self.save_btn.show(); self.cancel_btn.show()

            self._update_status_info()
            self._warn_password_due(new_id)
        except StaleSessionError:
            return                               # БД закрыта/сменена (lock/restore) — молча
        except Exception as e:                       # noqa: BLE001 — показать пользователю
            if gen == self._card_gen:
                self._show_card_error("Не удалось загрузить аккаунт", e)
        finally:
            if gen == self._card_gen:
                self._set_card_busy(False)

    async def _gallery_read_blob(self, image_id, session):
        """Async-чтение BLOB галереи для предпросмотра — через координатор БД с
        зафиксированным токеном сессии (H65-02). При смене сессии (lock/restore)
        run_async поднимает StaleSessionError — возвращаем None; предпросмотр к
        этому моменту уже погашен сменой поколения и результат не применит."""
        try:
            return await self.db.run_async(
                self.db.load_gallery_image, image_id, _session=session)
        except StaleSessionError:
            return None

    def _resolve_link_names(self, ids):
        """Имена связанных аккаунтов по их id (для отображения). Вызывается в
        фоновом потоке БД через run_async — get_account_path потокобезопасен."""
        return [{"id": i, "name": self.db.get_account_path(i)} for i in ids]

    def _resolve_fin_link_rows(self, ids):
        """Строки привязанных фин-записей по их id (восстановление из кеша
        правок). Вызывается в фоновом потоке БД; записи из корзины/удалённые
        отфильтровываются (list_fin_items отдаёт только живые)."""
        by_id = {r["id"]: r for r in self.db.list_fin_items()}
        return [by_id[i] for i in ids if i in by_id]

    def _collect_account_data(self):
        """Собирает AccountData из полей UI (без записи в БД)."""
        d = self.current_account_data
        # История паролей: если пароль был задан и изменился, дописываем строку
        # в конец заметок (см. _append_password_history).
        old_password = d.password
        new_password = self.tabs.f_password.get_text()
        notes = self._append_password_history(
            self.tabs.f_notes.get_text(), old_password, new_password)

        d.name = self.tabs.f_name.get_text()
        d.url = self.tabs.f_url.get_text()
        d.creation_date = self.tabs.f_creation_date.get_date()
        _pwd = self.tabs.f_password_date.get_date()
        d.password_changed_date = _pwd.date() if _pwd else None
        d.notes = notes
        d.login = self.tabs.f_login.get_text()
        d.password = new_password
        d.mobile_phone = self.tabs.f_mobile.get_text()
        d.first_name = self.tabs.f_first.get_text()
        d.last_name = self.tabs.f_last.get_text()
        d.middle_name = self.tabs.f_middle.get_text()
        _bd = self.tabs.f_birth.get_date()
        d.birth_date = _bd.date() if _bd else None
        d.address = self.tabs.f_address.get_text()
        d.recovery_phrase = self.tabs.f_recovery.get_text()
        d.device_id = self.tabs.f_device_id.get_text()
        d.ip = self.tabs.f_ip.get_text()
        d.browser = self.tabs.f_browser.get_text()
        d.os = self.tabs.f_os.get_text()
        d.password_change_interval_days = self.tabs.f_pwd_interval.get_value()
        d.secret_questions = self.tabs.f_questions_widget.get_data()
        d.one_time_codes = self.tabs.f_codes_widget.get_data()
        d.gallery = self.tabs.f_gallery_widget.get_data()
        return d

    def _append_password_history(self, notes, old_password, new_password):
        """Если пароль был непустым и изменился — дописывает в конец заметок
        строку истории (логика — domain.append_password_history) и обновляет
        поле заметок в интерфейсе. Возвращает итоговый текст заметок."""
        stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        updated = domain.append_password_history(
            notes, old_password, new_password, stamp)
        if updated != notes:
            self.tabs.f_notes.set_text(updated)   # отразить в UI сразу
        return updated

    def _stash_current_edits(self, account_id):
        """Сохраняет несохранённые правки аккаунта в память (не в БД).
        fin_links=None при выключенной опции — сохранение из такого черновика
        не тронет связи fin_links (см. _current_fin_link_ids)."""
        d = self._collect_account_data()
        self._edit_cache[(ACCOUNT, account_id)] = {
            "storage": d.to_storage(),
            "links": self.tabs.f_linked.get_data(),
            "fin_links": self._current_fin_link_ids(),
        }
        self._dirty_ids.add((ACCOUNT, account_id))

    def _refresh_dirty_markers(self, account_id=None):
        """Обновляет подписи и выделение элементов (метка несохранённых правок).

        С account_id — точечно, один элемент (обычный случай: сменился статус
        одного аккаунта). Без аргумента или если элемент не найден — полный
        обход, как раньше (безопасный fallback)."""
        if account_id is not None and self._refresh_account_item(account_id):
            return
        for it in self._iter_items():
            self._apply_item_style(it, self._node(it))

    def load_data_to_ui(self, links=None, other_bytes=None, fin_links=None):
        d = self.current_account_data
        self.tabs.f_name.set_text(d.name)
        self.tabs.f_url.set_text(d.url)
        self.tabs.f_creation_date.set_date(d.creation_date)
        self.tabs.f_password_date.set_date(d.password_changed_date)
        self.tabs.f_pwd_interval.set_value(d.password_change_interval_days)
        self.tabs.f_notes.set_text(d.notes)
        self.tabs.f_login.set_text(d.login)
        self.tabs.f_password.set_text(d.password)
        self.tabs.f_mobile.set_text(d.mobile_phone)
        self.tabs.f_first.set_text(d.first_name)
        self.tabs.f_last.set_text(d.last_name)
        self.tabs.f_middle.set_text(d.middle_name)
        self.tabs.f_birth.set_date(d.birth_date)
        self.tabs.f_address.set_text(d.address)
        self.tabs.f_recovery.set_text(d.recovery_phrase)
        self.tabs.f_device_id.set_text(d.device_id)
        self.tabs.f_ip.set_text(d.ip)
        self.tabs.f_browser.set_text(d.browser)
        self.tabs.f_os.set_text(d.os)

        self.tabs.f_questions_widget.set_data(d.secret_questions)
        self.tabs.f_codes_widget.set_data(d.one_time_codes)
        self.tabs.f_gallery_widget.set_data(d.gallery)
        # Ленивая загрузка BLOB: синхронный колбэк — для клика/get_data (БД
        # открыта), а фоновый предпросмотр читает через координатор БД с токеном
        # сессии, чтобы не читать чужую сессию после lock/restore (H65-02).
        self.tabs.f_gallery_widget.set_image_loader(self.db.load_gallery_image)
        self.tabs.f_gallery_widget.set_async_reader(
            self._gallery_read_blob, self.db.current_session)
        # Загрузка картинки переживает смену карточки (M7-05): виджет снимает id
        # аккаунта на старте импорта, а завершившийся после переключения результат
        # отдаёт через orphan-handler своему аккаунту (в живую карточку/черновик).
        self.tabs.f_gallery_widget.set_account_provider(
            lambda: self._current_account_id)
        self.tabs.f_gallery_widget.set_orphan_upload_handler(
            self._on_gallery_orphan_upload)
        # Лимит суммарного объёма галереи. В горячем пути объём прочих аккаунтов
        # уже посчитан асинхронно (other_bytes) — провайдер отдаёт готовое число,
        # без обращения к БД при добавлении картинки. Иначе (холодный путь) —
        # живой провайдер (синхронный запрос к БД при добавлении).
        aid = self._current_account_id
        if other_bytes is not None:
            self.tabs.f_gallery_widget.set_size_context(lambda v=other_bytes: v)
        elif aid is not None:
            self.tabs.f_gallery_widget.set_size_context(
                lambda a=aid: self.db.gallery_total_bytes(exclude_account_id=a))
        else:
            self.tabs.f_gallery_widget.set_size_context(None)

        # Связанные аккаунты: в горячем пути переданы готовыми (links), иначе —
        # синхронный запрос к БД (холодный путь / прямой вызов).
        if links is None:
            node = self._node(self.current_tree_item)
            links = self.db.get_links(node["id"]) if node else []
        self.tabs.f_linked.set_data(links)

        # Привязанные карты/кошельки (§8) — тот же горячий/холодный паттерн.
        # При выключенной опции секция скрыта — БД не читаем, список пуст.
        if fin_links is None:
            node = self._node(self.current_tree_item)
            fin_links = (self.db.get_account_fin_links(node["id"])
                         if node and self._fin_enabled() else [])
        self.tabs.f_fin_linked.set_data(fin_links)

    def toggle_edit_mode(self):
        if self._card_busy:
            return                              # идёт загрузка/сохранение — не входим в правку
        self.is_editing = True
        self.tabs.set_all_editable(True)
        # Переключаем кнопки
        self.edit_btn.hide()
        self.save_btn.show()
        self.cancel_btn.show()
        self._update_status_info()

    def cancel_edit(self):
        if self._card_busy:
            return
        self._set_card_busy(True)
        util.fire(self._cancel_edit_async(self._card_gen))

    async def _cancel_edit_async(self, gen):
        # Отмена отбрасывает несохранённые правки этого аккаунта
        session = self.db.current_session()
        aid = self._current_account_id
        self._edit_cache.pop((ACCOUNT, aid), None)
        self._dirty_ids.discard((ACCOUNT, aid))
        self.is_editing = False
        try:
            storage = await self.db.run_async(
                self.db.load_account, aid, _session=session)
            if gen != self._card_gen:
                return                          # пользователь уже переключился
            if storage is None:
                # Аккаунт исчез, пока шло редактирование — отменять нечего.
                self._current_account_id = None
                self._show_placeholder()
                self._update_status_info()
                self._reload_tree()
                return
            links = await self.db.run_async(self.db.get_links, aid, _session=session)
            fin_links = []
            if self._fin_enabled():
                fin_links = await self.db.run_async(
                    self.db.get_account_fin_links, aid, _session=session)
            other_bytes = await self.db.run_async(
                self.db.gallery_total_bytes, aid, _session=session)
            if gen != self._card_gen:
                return
            self.current_account_data = AccountData.from_storage(storage)
            self.load_data_to_ui(links=links, other_bytes=other_bytes,
                                 fin_links=fin_links)
            self.tabs.set_all_editable(False)
            self.edit_btn.show()
            self.save_btn.hide()
            self.cancel_btn.hide()
            self._update_status_info()
            # Убрать метку несохранённых правок — точечно, только у этого
            # аккаунта; имя/порядок в дереве отмена не меняет.
            if not self._refresh_account_item(aid):
                self._reload_tree()
        except StaleSessionError:
            return                               # БД закрыта/сменена — молча
        except Exception as e:                   # noqa: BLE001
            if gen == self._card_gen:
                self._show_card_error("Не удалось отменить правки", e)
        finally:
            if gen == self._card_gen:
                self._set_card_busy(False)

    def save_account(self):
        if self._card_busy:
            return                              # повторный Save во время записи запрещён
        self._set_card_busy(True)
        util.fire(self._save_account_async(self._card_gen))

    async def _save_account_async(self, gen):
        # Сбор данных из полей UI — синхронно (до первого await), снимок
        # согласован. get_data() может дочитать незагруженные BLOB (обычно уже
        # в кеше после предпросмотра).
        session = self.db.current_session()
        aid = self._current_account_id
        if aid is None:
            self._set_card_busy(False)
            return
        # H65-01: на всё время записи переводим поля карточки в read-only. Кнопки
        # уже заблокированы (_set_card_busy), но сами поля оставались editable —
        # текст, введённый во время await, не попадал бы ни в снимок, ни в БД и
        # тихо терялся. Блокируем ДО первого await (ожидание загрузок картинок).
        self.tabs.set_all_editable(False)
        # Дождаться незавершённых загрузок картинок: иначе get_data() пропустит
        # ещё не дочитанные BLOB и Save «потеряет» изображение (M6-01).
        gallery = self.tabs.f_gallery_widget
        if gallery.has_pending_uploads():
            self.statusBar().showMessage("Дождитесь загрузки изображений…", 2000)
            await gallery.wait_pending_uploads()
            if gen != self._card_gen or self._current_account_id != aid:
                return
        d = self._collect_account_data()
        storage = d.to_storage()
        link_ids = self.tabs.f_linked.get_data()
        # При выключенной опции показа фин-инструментов — None: скрытый пустой
        # виджет НЕ должен стереть реальные связи fin_links (КРИТИЧНЫЙ инвариант).
        fin_link_ids = self._current_fin_link_ids()
        # Запись карточки и всех связей (аккаунты + карты/кошельки) — атомарно,
        # в ОДНОЙ транзакции, в фоновом потоке.
        try:
            gallery_ids = await self.db.run_async(
                self.db.save_account_with_links, aid, storage, link_ids,
                fin_link_ids, _session=session)
        except StaleSessionError:
            # БД сменена во время записи (restore, вкл/выкл шифрования) — запись
            # не выполнена. Если карточка всё ещё открыта (gen совпал), её НЕЛЬЗЯ
            # оставлять busy/read-only: возвращаем черновик в кеш и разблокируем.
            if gen == self._card_gen:
                self._edit_cache[(ACCOUNT, aid)] = {"storage": storage, "links": link_ids,
                                                    "fin_links": fin_link_ids}
                self._dirty_ids.add((ACCOUNT, aid))
                self._refresh_dirty_markers(aid)
                self.tabs.set_all_editable(True)
                self._set_card_busy(False)
                self.statusBar().showMessage(
                    "База была переоткрыта — сохранение не выполнено, "
                    "правки в черновике. Повторите сохранение.", 6000)
            return
        except Exception as e:                   # noqa: BLE001
            if gen == self._card_gen:
                # Снимок не потерян: возвращаем его в кеш правок, чтобы пользователь
                # мог повторить сохранение, и показываем причину. Поля возвращаем в
                # editable — пользователь остаётся в режиме правки (H65-01).
                self._edit_cache[(ACCOUNT, aid)] = {"storage": storage, "links": link_ids,
                                                    "fin_links": fin_link_ids}
                self._dirty_ids.add((ACCOUNT, aid))
                self._refresh_dirty_markers(aid)
                self.tabs.set_all_editable(True)
                self._show_card_error("Не удалось сохранить аккаунт", e)
                self._set_card_busy(False)
            return

        self._any_db_changes = True
        # Кеш правок чистим ТОЛЬКО если за время await пользователь не переключился
        # и не перезанёс свежий черновик для aid (иначе потеряли бы новые правки, H6-02).
        if gen == self._card_gen:
            # Новые картинки получают id своих строк — повторное сохранение
            # обновит их, а не пересоздаст (лишняя перезапись BLOB).
            gallery.assign_saved_ids(gallery_ids)
            self._edit_cache.pop((ACCOUNT, aid), None)
            self._dirty_ids.discard((ACCOUNT, aid))
            self.is_editing = False
            self.tabs.set_all_editable(False)
            self.edit_btn.show()
            self.save_btn.hide()
            self.cancel_btn.hide()
            self._set_card_busy(False)
        # Обновляем узел дерева точечно (имя, метка срока пароля, снятие
        # маркера правок); полная перестройка — только если сохранение могло
        # изменить порядок сортировки (внутри метода).
        self._update_account_item_after_save(aid, storage["fields"])
        self.statusBar().showMessage("СОХРАНЕНО!", 2000)

    def generate_password(self):
        self.tabs.f_password.set_text(
            password_gen.generate_from_config(self.config))
        self.statusBar().showMessage("ПАРОЛЬ СГЕНЕРИРОВАН", 2000)

    def open_password_generator_settings(self):
        """Диалог «ПАРАМЕТРЫ ГЕНЕРАЦИИ»: при OK диалог уже записал настройки
        в config — сохраняем файл и подставляем пароль из предпросмотра в поле
        пароля карточки (кнопка доступна только в режиме редактирования)."""
        dlg = GeneratorSettingsDialog(self.config, self)
        if dlg.exec() == QDialog.Accepted:
            self.config.save()
            self.tabs.f_password.set_text(dlg.preview_text())
            self.statusBar().showMessage(
                "НАСТРОЙКИ СОХРАНЕНЫ, ПАРОЛЬ ПОДСТАВЛЕН", 2000)

    def generate_personal_data(self):
        choice, ok = theme.themed_combo_choice(
            self.config, self, "Генерация ПД", "Выберите национальность:",
            ["Русский", "Американец"],
        )
        if not ok:
            return
        lang = "ru" if choice == "Русский" else "en"
        person = pd_generator.generate_person(lang)

        self.tabs.f_first.set_text(person["first"])
        self.tabs.f_last.set_text(person["last"])
        self.tabs.f_middle.set_text(person["middle"])
        # birth_date из генератора — стандартный datetime.date, поле теперь
        # принимает его напрямую (граница Qt — внутри CopyableDateField).
        self.tabs.f_birth.set_date(person["birth_date"])
        self.tabs.f_address.set_text(person["address"])
        self.statusBar().showMessage("ПД СГЕНЕРИРОВАНЫ", 2000)

    # ----- Связанные аккаунты -----

    def on_link_navigate(self, account_id):
        self._select_node(ACCOUNT, account_id)

    def on_add_link_requested(self):
        node = self._node(self.current_tree_item)
        if not node:
            return
        # Чтение всех аккаунтов — в фоновом потоке БД (H-8): на больших базах
        # get_all_accounts не должен вешать UI. Модальный выбор показываем уже
        # после загрузки кандидатов (сам диалог остаётся синхронным — его
        # результат нужен по месту).
        util.fire(self._add_link_async(node["id"]))

    async def _add_link_async(self, node_id):
        session = self.db.current_session()
        try:
            accounts = await self.db.run_async(
                self.db.get_all_accounts, _session=session)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            self._show_card_error("Не удалось загрузить список аккаунтов", e)
            return
        candidates = [a for a in accounts if a["id"] != node_id]
        chosen, ok = theme.themed_multiselect(
            self.config, self, "Связать аккаунты", candidates,
            self.tabs.f_linked.get_data()
        )
        if ok:
            chosen_set = set(chosen)
            links = [{"id": a["id"], "name": a["name"]}
                     for a in candidates if a["id"] in chosen_set]
            self.tabs.f_linked.set_data(links)
            self.tabs.f_linked.set_editable(self.is_editing)

    # ----- Привязанные карты/кошельки (§8) -----

    def on_fin_link_navigate(self, node_type, item_id):
        """Клик по привязанной записи — выделить её узел в дереве."""
        self._select_node(node_type, item_id)

    def on_add_fin_link_requested(self):
        """«+ ПРИВЯЗАТЬ» на карточке аккаунта: выбор из живых фин-записей.
        Чтение списка — в фоновом потоке БД (H-8), диалог — после загрузки."""
        if self._node(self.current_tree_item) is None:
            return
        util.fire(self._add_fin_link_async())

    async def _add_fin_link_async(self):
        session = self.db.current_session()
        try:
            items = await self.db.run_async(
                self.db.list_fin_items, _session=session)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            self._show_card_error("Не удалось загрузить список записей", e)
            return
        candidates = [{"id": it["id"], "name": fin_item_display(it)}
                      for it in items]
        chosen, ok = theme.themed_multiselect(
            self.config, self, "Привязать карты и кошельки", candidates,
            self.tabs.f_fin_linked.get_data())
        if ok:
            chosen_set = set(chosen)
            links = [it for it in items if it["id"] in chosen_set]
            self.tabs.f_fin_linked.set_data(links)
            self.tabs.f_fin_linked.set_editable(self.is_editing)

    # ----- Статус-бар -----

    def _update_status_info(self):
        node = self._node(self.current_tree_item)
        if not node or node["type"] != ACCOUNT:
            self.status_info.setText("")
            return
        path = self.db.get_account_path(node["id"])
        prefix = "[РЕД] " if self.is_editing else "[ПРОСМОТР] "
        self.status_info.setText(prefix + path)

    def _warn_password_due(self, account_id):
        node = self._node(self.current_tree_item)
        days = node.get("pwd_days_left") if node else None
        if days is None:
            return
        if days <= 0:
            self.statusBar().showMessage("ПОРА СМЕНИТЬ ПАРОЛЬ ДЛЯ ЭТОГО АККАУНТА!", 5000)
        elif days <= 7:
            self.statusBar().showMessage(f"Смена пароля через {days} дн.", 4000)
