"""Карточка аккаунта главного окна (Эпик 4.4).

Примесь (mixin) с логикой правой панели: загрузка/сохранение карточки, режим
редактирования, кеш несохранённых правок, связанные аккаунты, генерация пароля
и ПД, обновление строки статуса. Состояние разделяется с окном через self —
поведение идентично прежнему. Подмешивается в MainWindow перед QMainWindow."""
from PySide6.QtCore import QDate, QDateTime

from models import AccountData
import pd_generator
import util
import domain
import theme


class AccountCardMixin:
    """Правая панель: просмотр/редактирование карточки, связи, генерация, статус."""

    def _show_placeholder(self):
        self.placeholder_label.show()
        self.tabs.hide()
        self.edit_btn.hide()
        self.save_btn.hide()
        self.cancel_btn.hide()

    def on_item_selected(self, current, previous):
        node = self._node(current)
        new_id = node["id"] if (node and node["type"] == "account") else None

        # Уходим с аккаунта, который сейчас редактируется → стэшим правки (не теряем их)
        if (self.is_editing and self._current_account_id is not None
                and self._current_account_id != new_id):
            self._stash_current_edits(self._current_account_id)
            self._refresh_dirty_markers()

        if new_id is None:
            self._current_account_id = None
            self.is_editing = False
            self._show_placeholder()
            self._update_status_info()
            return

        self.placeholder_label.hide()
        self.tabs.show()
        self.current_tree_item = current
        self._current_account_id = new_id

        if new_id in self._edit_cache:
            # Возврат к аккаунту с несохранёнными правками — восстанавливаем
            cached = self._edit_cache[new_id]
            self.current_account_data = AccountData.from_storage(cached["storage"])
            self.load_data_to_ui()
            links = [{"id": i, "name": self.db.get_account_path(i)} for i in cached["links"]]
            self.tabs.f_linked.set_data(links)
            self.is_editing = True
            self.tabs.set_all_editable(True)
            self.edit_btn.hide(); self.save_btn.show(); self.cancel_btn.show()
        else:
            self.current_account_data = AccountData.from_storage(self.db.load_account(new_id))
            self.is_editing = False
            self.load_data_to_ui()
            self.tabs.set_all_editable(False)
            self.edit_btn.show(); self.save_btn.hide(); self.cancel_btn.hide()

        self._update_status_info()
        self._warn_password_due(new_id)

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
        """Сохраняет несохранённые правки аккаунта в память (не в БД)."""
        d = self._collect_account_data()
        self._edit_cache[account_id] = {
            "storage": d.to_storage(),
            "links": self.tabs.f_linked.get_data(),
        }
        self._dirty_ids.add(account_id)

    def _refresh_dirty_markers(self):
        """Обновляет подписи и выделение элементов (метка несохранённых правок)."""
        for it in self._iter_items():
            self._apply_item_style(it, self._node(it))

    def load_data_to_ui(self):
        d = self.current_account_data
        self.tabs.f_name.set_text(d.name)
        self.tabs.f_url.set_text(d.url)
        self.tabs.f_creation_date.set_date(d.creation_date)
        self.tabs.f_password_date.set_date(d.password_changed_date)  # Теперь просто передаем QDate
        self.tabs.f_pwd_interval.set_value(d.password_change_interval_days)
        self.tabs.f_notes.set_text(d.notes)
        self.tabs.f_login.set_text(d.login)
        self.tabs.f_password.set_text(d.password)
        self.tabs.f_first.set_text(d.first_name)
        self.tabs.f_last.set_text(d.last_name)
        self.tabs.f_middle.set_text(d.middle_name)
        self.tabs.f_birth.set_date(d.birth_date)  # Теперь просто передаем QDate
        self.tabs.f_address.set_text(d.address)
        self.tabs.f_recovery.set_text(d.recovery_phrase)
        self.tabs.f_device_id.set_text(d.device_id)
        self.tabs.f_ip.set_text(d.ip)
        self.tabs.f_browser.set_text(d.browser)
        self.tabs.f_os.set_text(d.os)

        self.tabs.f_questions_widget.set_data(d.secret_questions)
        self.tabs.f_codes_widget.set_data(d.one_time_codes)
        self.tabs.f_gallery_widget.set_data(d.gallery)
        # Ленивая загрузка BLOB: виджет запросит байты конкретного изображения
        # только когда пользователь кликнет на него или при сохранении.
        self.tabs.f_gallery_widget.set_image_loader(self.db.load_gallery_image)
        # Лимит суммарного объёма галереи: провайдер берёт из БД объём картинок
        # ОСТАЛЬНЫХ аккаунтов (текущий держится в памяти карточки — не дублируем).
        aid = self._current_account_id
        self.tabs.f_gallery_widget.set_size_context(
            (lambda a=aid: self.db.gallery_total_bytes(exclude_account_id=a))
            if aid is not None else None)

        # Связанные аккаунты грузим напрямую из БД (это отношение, не поле аккаунта)
        node = self._node(self.current_tree_item)
        self.tabs.f_linked.set_data(self.db.get_links(node["id"]) if node else [])

    def toggle_edit_mode(self):
        self.is_editing = True
        self.tabs.set_all_editable(True)
        # Переключаем кнопки
        self.edit_btn.hide()
        self.save_btn.show()
        self.cancel_btn.show()
        self._update_status_info()

    def cancel_edit(self):
        # Отмена отбрасывает несохранённые правки этого аккаунта
        aid = self._current_account_id
        self._edit_cache.pop(aid, None)
        self._dirty_ids.discard(aid)

        self.is_editing = False
        self.current_account_data = AccountData.from_storage(self.db.load_account(aid))
        self.load_data_to_ui()
        self.tabs.set_all_editable(False)
        self.edit_btn.show()
        self.save_btn.hide()
        self.cancel_btn.hide()
        self._update_status_info()
        self._reload_tree()  # убрать метку несохранённых правок

    def save_account(self):
        d = self._collect_account_data()
        aid = self._current_account_id
        self.db.save_account(aid, d.to_storage())
        self.db.set_links(aid, self.tabs.f_linked.get_data())

        self._edit_cache.pop(aid, None)
        self._dirty_ids.discard(aid)
        self._any_db_changes = True

        self.is_editing = False
        self.tabs.set_all_editable(False)
        self.edit_btn.show()
        self.save_btn.hide()
        self.cancel_btn.hide()
        # Перестраиваем дерево, чтобы обновить имя/маркеры (избранное, срок пароля)
        self._reload_tree()
        self.statusBar().showMessage("СОХРАНЕНО!", 2000)

    def generate_password(self):
        self.tabs.f_password.set_text(util.generate_password())
        self.statusBar().showMessage("ПАРОЛЬ СГЕНЕРИРОВАН", 2000)

    def generate_personal_data(self):
        choice, ok = theme.themed_choice(
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
        bd = person["birth_date"]
        self.tabs.f_birth.set_date(QDate(bd.year, bd.month, bd.day))
        self.statusBar().showMessage("ПД СГЕНЕРИРОВАНЫ", 2000)

    # ----- Связанные аккаунты -----

    def on_link_navigate(self, account_id):
        self._select_node("account", account_id)

    def on_add_link_requested(self):
        node = self._node(self.current_tree_item)
        if not node:
            return
        candidates = [a for a in self.db.get_all_accounts() if a["id"] != node["id"]]
        chosen, ok = theme.themed_multiselect(
            self.config, self, "Связать аккаунты", candidates, self.tabs.f_linked.get_data()
        )
        if ok:
            chosen_set = set(chosen)
            links = [{"id": a["id"], "name": a["name"]} for a in candidates if a["id"] in chosen_set]
            self.tabs.f_linked.set_data(links)
            self.tabs.f_linked.set_editable(self.is_editing)

    # ----- Статус-бар -----

    def _update_status_info(self):
        node = self._node(self.current_tree_item)
        if not node or node["type"] != "account":
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
