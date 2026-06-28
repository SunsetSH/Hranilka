"""Дерево и навигация главного окна (Эпик 4.4).

Содержит сам виджет дерева (AccountTree с ограниченным drag&drop) и примесь
TreeMixin: построение/перестроение дерева, сортировку, живой фильтр, контекстное
меню и операции над папками/сервисами/аккаунтами (создание, переименование,
перемещение, удаление). Состояние разделяется с окном через self — поведение
идентично прежнему. Подмешивается в MainWindow перед QMainWindow."""
from PySide6.QtWidgets import (QTreeWidget, QTreeWidgetItem, QMenu,
                               QAbstractItemView)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush

import theme
from models import AccountData


class AccountTree(QTreeWidget):
    """Дерево с ограниченным drag&drop (вариант A): перетаскиванием можно менять
    порядок только среди соседей ТОГО ЖЕ ТИПА и ТОГО ЖЕ родителя — включая
    верхний уровень (папки среди папок, корневые сервисы среди корневых сервисов,
    корневые аккаунты среди корневых аккаунтов). Перенос между родителями и смена
    типа — через контекстное меню."""

    order_changed = Signal(object)   # параметр: элемент-родитель (или None для корня)
    drop_rejected = Signal()

    @staticmethod
    def _item_type(item):
        if item is None:
            return None
        data = item.data(0, Qt.UserRole)
        return data["type"] if data else None

    def dropEvent(self, event):
        sources = self.selectedItems()
        if not sources:
            event.ignore()
            return

        # Все перетаскиваемые элементы — одного родителя и одного типа
        src_parents = {it.parent() for it in sources}
        src_types = {self._item_type(it) for it in sources}
        if len(src_parents) != 1 or len(src_types) != 1:
            event.ignore(); self.drop_rejected.emit(); return
        src_parent = next(iter(src_parents))
        src_type = next(iter(src_types))

        # Куда бросаем: только «между строками» (Above/Below), не «внутрь»
        target = self.itemAt(event.position().toPoint())
        indicator = self.dropIndicatorPosition()
        if indicator not in (QAbstractItemView.AboveItem, QAbstractItemView.BelowItem):
            event.ignore(); self.drop_rejected.emit(); return
        dest_parent = target.parent() if target else None
        dest_type = self._item_type(target)

        # Тот же родитель И тот же тип, что у строки-цели
        if dest_parent is not src_parent or dest_type != src_type:
            event.ignore(); self.drop_rejected.emit(); return

        super().dropEvent(event)
        self.order_changed.emit(src_parent)


class TreeMixin:
    """Построение/перестроение дерева, сортировка, поиск, контекстное меню и
    операции над папками/сервисами/аккаунтами."""

    # Префиксы-«иконки» для элементов дерева
    PREFIX = {"folder": "[+] ", "service": "[o] ", "account": "(i) "}

    def _node_display(self, node):
        """Текст элемента дерева с маркерами избранного, просрочки и несохранённых правок."""
        text = self.PREFIX[node["type"]] + node["name"]
        if node["type"] == "account":
            if node.get("is_favorite"):
                text = "* " + text
            days = node.get("pwd_days_left")
            if days is not None and days <= 0:
                text += "  [!]"   # пора менять пароль
            if node["id"] in self._dirty_ids:
                text = "● НЕ СОХРАНЕНО ▸ " + text   # есть несохранённые правки
        return text

    def _apply_item_style(self, item, node):
        """Текст + визуальное выделение для аккаунтов с несохранёнными правками."""
        item.setText(0, self._node_display(node))
        dirty = node["type"] == "account" and node["id"] in self._dirty_ids
        font = item.font(0)
        font.setBold(dirty)
        item.setFont(0, font)
        item.setForeground(0, QBrush(QColor("#FFC400")) if dirty else QBrush())

    def _node(self, item):
        """Возвращает словарь данных (type, id, name, ...) элемента дерева или None."""
        return item.data(0, Qt.UserRole) if item else None

    # ----- Сортировка / поиск / DnD -----

    def _update_dnd_mode(self):
        """DnD-переупорядочивание доступно только в режиме ручной сортировки.
        В остальных режимах перетаскивание полностью выключаем (и сам drag, и
        приём drop), иначе тянущийся элемент превращался бы в выделение."""
        manual = self.sort_mode == "manual"
        self.tree.setDragEnabled(manual)
        self.tree.setDragDropMode(QAbstractItemView.InternalMove if manual
                                  else QAbstractItemView.NoDragDrop)

    def on_sort_changed(self):
        self.sort_mode = self.sort_combo.currentData()
        self.config.set("sort_mode", self.sort_mode)
        self.config.save()
        self._update_dnd_mode()
        self._update_sort_dir_btn()
        self._reload_tree()

    def _update_sort_dir_btn(self):
        self.sort_dir_btn.setText("v" if self.sort_desc else "^")
        # Направление неактуально для ручной сортировки
        self.sort_dir_btn.setEnabled(self.sort_mode != "manual")

    def on_sort_dir_toggled(self):
        self.sort_desc = not self.sort_desc
        self.config.set("sort_desc", self.sort_desc)
        self.config.save()
        self._update_sort_dir_btn()
        self._reload_tree()

    def on_search_changed(self, text):
        self.search_text = text.strip().lower()
        self._apply_filter()

    def _apply_filter(self):
        text = self.search_text

        def visit(item):
            node = self._node(item)
            self_match = (not text) or (text in node["name"].lower())
            child_visible = False
            for i in range(item.childCount()):
                child_visible = visit(item.child(i)) or child_visible
            visible = self_match or child_visible
            item.setHidden(not visible)
            if text and child_visible:
                item.setExpanded(True)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def on_tree_order_changed(self, parent_item):
        """Сохраняет новый порядок после перетаскивания.

        Для корня (parent_item is None) порядок сохраняется по каждой группе
        типов отдельно (папки / корневые сервисы / корневые аккаунты), т.к. они
        хранятся в разных таблицах со своим sort_order."""
        if parent_item is None:
            folder_ids, service_ids, account_ids = [], [], []
            for i in range(self.tree.topLevelItemCount()):
                node = self._node(self.tree.topLevelItem(i))
                {"folder": folder_ids, "service": service_ids,
                 "account": account_ids}[node["type"]].append(node["id"])
            self.db.set_folders_order(folder_ids)
            self.db.set_services_order(service_ids)   # корневые сервисы
            self.db.set_accounts_order(account_ids)   # корневые аккаунты
            self.statusBar().showMessage("Порядок сохранён", 2000)
            return

        parent_node = self._node(parent_item)
        child_ids = [self._node(parent_item.child(i))["id"]
                     for i in range(parent_item.childCount())]
        if parent_node["type"] == "folder":
            self.db.set_services_order(child_ids)
        elif parent_node["type"] == "service":
            self.db.set_accounts_order(child_ids)
        self.statusBar().showMessage("Порядок сохранён", 2000)

    # ----- Построение дерева -----

    def populate_tree(self):
        # Отключаем перерисовку на время массовой вставки: дерево не
        # перерисовывается на каждый добавленный узел, а один раз в конце —
        # заметно быстрее на больших базах (ускорение запуска).
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.clear()
            for node in self.db.get_tree_structure(self.sort_mode, self.sort_desc):
                self._add_tree_node(self.tree, node)
        finally:
            self.tree.setUpdatesEnabled(True)
        self._apply_filter()

    def _add_tree_node(self, parent, node):
        data = {k: v for k, v in node.items() if k != "children"}
        item = QTreeWidgetItem(parent, [""])
        item.setData(0, Qt.UserRole, data)
        item.setToolTip(0, node["name"])
        self._apply_item_style(item, node)
        if node["type"] in ("folder", "service"):
            item.setExpanded(True)
        for child in node.get("children", []):
            self._add_tree_node(item, child)
        return item

    def _iter_items(self):
        """Обход всех элементов дерева."""
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(item.child(i) for i in range(item.childCount()))

    def _reload_tree(self):
        """Перестраивает дерево из БД, сохраняя раскрытие и выбранный элемент.

        Сигналы дерева блокируются, чтобы перестроение не вызывало повторную
        загрузку правой панели и логику стэша несохранённых правок."""
        expanded = {(self._node(it)["type"], self._node(it)["id"])
                    for it in self._iter_items() if it.isExpanded()}
        sel = self._node(self.tree.currentItem())
        sel_key = (sel["type"], sel["id"]) if sel else None

        self.tree.blockSignals(True)
        self.populate_tree()

        for it in self._iter_items():
            node = self._node(it)
            if node["type"] in ("folder", "service"):
                it.setExpanded((node["type"], node["id"]) in expanded)
            if sel_key and (node["type"], node["id"]) == sel_key:
                self.tree.setCurrentItem(it)
                self.current_tree_item = it
        self.tree.blockSignals(False)

        # Восстановление раскрытия могло «свернуть» совпадения — применяем фильтр снова
        if self.search_text:
            self._apply_filter()

    def _select_node(self, node_type, node_id):
        for it in self._iter_items():
            n = self._node(it)
            if n["type"] == node_type and n["id"] == node_id:
                self.tree.setCurrentItem(it)
                return

    # ----- Контекстное меню -----

    def show_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        selected = self.tree.selectedItems()
        if item not in selected:
            self.tree.setCurrentItem(item)
            selected = [item]
        nodes = [self._node(i) for i in selected]
        types = {n["type"] for n in nodes}
        menu = QMenu(self)

        if len(selected) == 1:
            node = nodes[0]
            t = node["type"]
            if t == "account":
                self._add_move_to_service_menu(menu, selected)
                fav = node.get("is_favorite")
                menu.addAction("Убрать из избранного" if fav else "В избранное",
                               lambda: self._set_favorite(selected, not fav))
                menu.addSeparator()
                menu.addAction("Экспорт…", lambda: self.open_export(node))
                menu.addAction("Удалить", lambda: self.delete_items(selected))
            elif t == "service":
                menu.addAction("Переименовать", lambda: self.rename_item(item))
                self._add_move_to_folder_menu(menu, selected)
                self._add_delete_menu(menu, selected, with_keep=True)
                menu.addSeparator()
                menu.addAction("Экспорт…", lambda: self.open_export(node))
                menu.addAction("Раскрыть всё", lambda: self.set_expanded(item, True))
                menu.addAction("Свернуть всё", lambda: self.set_expanded(item, False))
            elif t == "folder":
                menu.addAction("Переименовать", lambda: self.rename_item(item))
                self._add_delete_menu(menu, selected, with_keep=True)
                menu.addSeparator()
                menu.addAction("Экспорт…", lambda: self.open_export(node))
                menu.addAction("Раскрыть всё", lambda: self.set_expanded(item, True))
                menu.addAction("Свернуть всё", lambda: self.set_expanded(item, False))
        else:
            # Множественный выбор
            if types == {"service"}:
                self._add_move_to_folder_menu(menu, selected)
                self._add_delete_menu(menu, selected, with_keep=True)
            elif types == {"account"}:
                self._add_move_to_service_menu(menu, selected)
                menu.addAction("В избранное", lambda: self._set_favorite(selected, True))
                menu.addAction("Убрать из избранного", lambda: self._set_favorite(selected, False))
                menu.addSeparator()
                menu.addAction("Удалить", lambda: self.delete_items(selected))
            else:
                menu.addAction("Удалить", lambda: self.delete_items(selected))

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _add_delete_menu(self, menu, selected, with_keep):
        menu.addSeparator()
        if with_keep:
            sub = menu.addMenu("Удалить")
            sub.addAction("С сохранением содержимого",
                          lambda: self.delete_items(selected, keep=True))
            sub.addAction("Без сохранения (полностью)",
                          lambda: self.delete_items(selected, keep=False))
        else:
            menu.addAction("Удалить", lambda: self.delete_items(selected))

    def _add_move_to_folder_menu(self, menu, selected):
        sub = menu.addMenu("Переместить в папку")
        sub.addAction("Новая папка…", lambda: self._move_services_new_folder(selected))
        folders = self.db.get_folders()
        if folders:
            sub.addSeparator()
            for f in folders:
                sub.addAction(f["name"],
                              lambda checked=False, fid=f["id"]: self._move_services(selected, fid))
        sub.addSeparator()
        sub.addAction("Вынести из папки", lambda: self._move_services(selected, None))

    def _add_move_to_service_menu(self, menu, selected):
        sub = menu.addMenu("Переместить в сервис")
        sub.addAction("Новый сервис…", lambda: self._move_accounts_new_service(selected))
        # Текущие сервисы выбранных аккаунтов (по их родителю в дереве): сервис,
        # в котором уже находятся ВСЕ выбранные, не предлагаем (перенос — no-op).
        current = set()
        for it in selected:
            parent = it.parent()
            pnode = self._node(parent) if parent else None
            current.add(pnode["id"] if pnode and pnode["type"] == "service" else None)
        services = [s for s in self.db.get_services() if current != {s["id"]}]
        if services:
            sub.addSeparator()
            for s in services:
                sub.addAction(s["name"],
                              lambda checked=False, sid=s["id"]: self._move_accounts(selected, sid))
        sub.addSeparator()
        sub.addAction("Сделать свободным (без сервиса)",
                      lambda: self._move_accounts(selected, None))

    # ----- Операции меню -----

    def rename_item(self, item):
        node = self._node(item)
        new_name, ok = theme.themed_input(
            self.config, self, "Переименование", "Новое название:", node["name"]
        )
        if ok and new_name:
            if node["type"] == "folder":
                self.db.rename_folder(node["id"], new_name)
            elif node["type"] == "service":
                self.db.rename_service(node["id"], new_name)
            self._reload_tree()

    def set_expanded(self, item, state):
        item.setExpanded(state)
        for i in range(item.childCount()):
            self.set_expanded(item.child(i), state)

    def delete_items(self, selected, keep=False):
        nodes = [self._node(i) for i in selected]
        if len(nodes) == 1:
            msg = f"Удалить '{nodes[0]['name']}'?"
        else:
            msg = f"Удалить выбранные элементы ({len(nodes)} шт.)?"
        if keep:
            msg += "\nСодержимое будет сохранено (перемещено на уровень выше)."
        if not theme.themed_confirm(self.config, self, "Подтверждение", msg):
            return

        to_bin = self.config.get("recycle_bin_enabled", False)
        for node in nodes:
            if node["type"] == "folder":
                # При полном удалении (не keep) аккаунты внутри тоже исчезают —
                # чистим их кэш несохранённых правок, иначе остаётся «мусор» и
                # ложное предупреждение о несохранённых данных.
                if not keep:
                    self._forget_account_cache(
                        self.db.get_descendant_account_ids("folder", node["id"]))
                (self.db.delete_folder_keep_content if keep else self.db.delete_folder)(node["id"])
            elif node["type"] == "service":
                if not keep:
                    self._forget_account_cache(
                        self.db.get_descendant_account_ids("service", node["id"]))
                (self.db.delete_service_keep_content if keep else self.db.delete_service)(node["id"])
            else:
                # Аккаунты при включённой корзине удаляются мягко (в корзину).
                if to_bin:
                    self.db.move_account_to_bin(node["id"])
                else:
                    self.db.delete_account(node["id"])
                self._forget_account_cache([node["id"]])
        self._any_db_changes = True

        self.current_tree_item = None
        self._current_account_id = None
        self.is_editing = False
        self._reload_tree()
        self._show_placeholder()
        self._update_bin_button()
        self.statusBar().showMessage("Удалено", 2000)

    def _forget_account_cache(self, account_ids):
        """Удаляет несохранённые правки/пометки указанных аккаунтов из памяти."""
        for aid in account_ids:
            self._edit_cache.pop(aid, None)
            self._dirty_ids.discard(aid)

    def _set_favorite(self, selected, value):
        for node in (self._node(i) for i in selected):
            if node["type"] == "account":
                self.db.set_favorite(node["id"], value)
        self._reload_tree()

    def _move_services(self, selected, folder_id):
        for node in (self._node(i) for i in selected):
            if node["type"] == "service":
                self.db.move_service(node["id"], folder_id)
        self._reload_tree()
        self.statusBar().showMessage("Перемещено", 2000)

    def _move_services_new_folder(self, selected):
        name, ok = theme.themed_input(self.config, self, "Новая папка", "Название:")
        if ok and name:
            folder_id = self.db.add_folder(name)
            self._move_services(selected, folder_id)

    def _move_accounts(self, selected, service_id):
        for node in (self._node(i) for i in selected):
            if node["type"] == "account":
                self.db.move_account(node["id"], service_id)
        self._reload_tree()
        self.statusBar().showMessage("Перемещено", 2000)

    def _move_accounts_new_service(self, selected):
        name, ok = theme.themed_input(self.config, self, "Новый сервис", "Название:")
        if ok and name:
            service_id = self.db.add_service(name, None)
            self._move_accounts(selected, service_id)

    # ----- Добавление элементов -----

    def add_folder(self):
        name, ok = theme.themed_input(self.config, self, "Новая папка", "Название:")
        if ok and name:
            folder_id = self.db.add_folder(name)
            self._any_db_changes = True
            self._reload_tree()
            self._select_node("folder", folder_id)

    def add_service(self):
        # Сервис можно добавить в выбранную папку либо как самостоятельный
        # (вне папки) — оба варианта допускаются ТЗ.
        current = self.tree.currentItem()
        node = self._node(current)
        folder_id = node["id"] if node and node["type"] == "folder" else None

        name, ok = theme.themed_input(self.config, self, "Новый сервис", "Название:")
        if ok and name:
            service_id = self.db.add_service(name, folder_id)
            self._any_db_changes = True
            self._reload_tree()
            self._select_node("service", service_id)

    def add_account(self):
        current = self.tree.currentItem()
        node = self._node(current)
        if node and node["type"] == "service":
            service_id = node["id"]
        elif node and node["type"] == "account":
            # Аккаунт под сервисом → тот же сервис; свободный аккаунт → корень.
            parent = current.parent()
            pnode = self._node(parent) if parent else None
            service_id = pnode["id"] if pnode and pnode["type"] == "service" else None
        else:
            # Папка или ничего не выбрано → свободный аккаунт (в корне).
            service_id = None

        name, ok = theme.themed_input(self.config, self, "Новый аккаунт", "Название:")
        if ok and name:
            account_id = self.db.add_account(service_id, name)
            data = AccountData()
            data.name = name
            self.db.save_account(account_id, data.to_storage())  # поля по умолчанию
            self._any_db_changes = True
            self._reload_tree()
            self._select_node("account", account_id)
