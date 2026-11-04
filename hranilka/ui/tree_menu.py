"""Контекстное меню дерева (выделено из tree.py): примесь TreeMenuMixin."""
from hranilka.core.nodetypes import (FOLDER, SERVICE, ACCOUNT, SERVER,
                                     FIN_LEAF_TYPES, LEAF_TYPES)
from hranilka.core.fin_types import FIN_TYPES


class TreeMenuMixin:
    """Построение контекстного меню; операции (move/favorite/delete) — в TreeMixin."""

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
        menu = self._make_menu()

        if len(selected) == 1:
            node = nodes[0]
            t = node["type"]
            if t == ACCOUNT:
                self._add_move_to_service_menu(menu, selected)
                self._add_move_leaf_to_folder_menu(menu, selected)
                fav = node.get("is_favorite")
                menu.addAction("Убрать из избранного" if fav else "В избранное",
                               lambda: self._set_favorite(selected, not fav))
                menu.addSeparator()
                menu.addAction("Экспорт…", lambda: self.open_export(node))
                menu.addAction("Удалить", lambda: self.delete_items(selected))
            elif t in FIN_LEAF_TYPES:
                self._add_move_fin_to_service_menu(menu, selected)
                self._add_move_leaf_to_folder_menu(menu, selected)
                fav = node.get("is_favorite")
                menu.addAction("Убрать из избранного" if fav else "В избранное",
                               lambda: self._set_fin_favorite(selected, not fav))
                menu.addSeparator()
                menu.addAction("Экспорт…", lambda: self.open_export(node))
                menu.addAction("Удалить", lambda: self.delete_items(selected))
            elif t == SERVER:
                # Те же пункты, что у фин-листа, плюс переименование (правка
                # имени доступна и отсюда, независимо от карточки).
                self._add_move_server_to_service_menu(menu, selected)
                self._add_move_leaf_to_folder_menu(menu, selected)
                fav = node.get("is_favorite")
                menu.addAction("Убрать из избранного" if fav else "В избранное",
                               lambda: self._set_server_favorite(selected, not fav))
                menu.addSeparator()
                menu.addAction("Экспорт…", lambda: self.open_export(node))
                menu.addAction("Переименовать", lambda: self.rename_item(item))
                menu.addAction("Удалить", lambda: self.delete_items(selected))
            elif t == SERVICE:
                self._add_create_record_menu(menu)
                menu.addAction("Переименовать", lambda: self.rename_item(item))
                self._add_move_to_folder_menu(menu, selected)
                self._add_delete_menu(menu, selected, with_keep=True)
                menu.addSeparator()
                menu.addAction("Экспорт…", lambda: self.open_export(node))
                menu.addAction("Раскрыть всё", lambda: self.set_expanded(item, True))
                menu.addAction("Свернуть всё", lambda: self.set_expanded(item, False))
            elif t == FOLDER:
                self._add_create_record_menu(menu)
                menu.addAction("Переименовать", lambda: self.rename_item(item))
                self._add_delete_menu(menu, selected, with_keep=True)
                menu.addSeparator()
                menu.addAction("Экспорт…", lambda: self.open_export(node))
                menu.addAction("Раскрыть всё", lambda: self.set_expanded(item, True))
                menu.addAction("Свернуть всё", lambda: self.set_expanded(item, False))
        else:
            # Множественный выбор
            menu.addAction("Экспорт выделенного…",
                           lambda: self.open_selected_export(nodes))
            menu.addSeparator()
            if types == {SERVICE}:
                self._add_move_to_folder_menu(menu, selected)
                self._add_delete_menu(menu, selected, with_keep=True)
            elif types == {ACCOUNT}:
                self._add_move_to_service_menu(menu, selected)
                self._add_move_leaf_to_folder_menu(menu, selected)
                menu.addAction("В избранное", lambda: self._set_favorite(selected, True))
                menu.addAction("Убрать из избранного", lambda: self._set_favorite(selected, False))
                menu.addSeparator()
                menu.addAction("Удалить", lambda: self.delete_items(selected))
            elif types == {SERVER}:
                self._add_move_server_to_service_menu(menu, selected)
                self._add_move_leaf_to_folder_menu(menu, selected)
                menu.addAction("В избранное", lambda: self._set_server_favorite(selected, True))
                menu.addAction("Убрать из избранного",
                               lambda: self._set_server_favorite(selected, False))
                menu.addSeparator()
                menu.addAction("Удалить", lambda: self.delete_items(selected))
            else:
                if types <= LEAF_TYPES:      # смесь листьев: перенос в папку
                    self._add_move_leaf_to_folder_menu(menu, selected)
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
            current.add(pnode["id"] if pnode and pnode["type"] == SERVICE else None)
        services = [s for s in self.db.get_services() if current != {s["id"]}]
        if services:
            sub.addSeparator()
            for s in services:
                sub.addAction(s["name"],
                              lambda checked=False, sid=s["id"]: self._move_accounts(selected, sid))
        sub.addSeparator()
        sub.addAction("Сделать свободным (без сервиса)",
                      lambda: self._move_accounts(selected, None))

    def _add_move_leaf_to_folder_menu(self, menu, selected):
        """Перенос листьев (аккаунт/карта/кошелёк/сервер) прямо в папку,
        минуя сервис. Папка, в которой уже лежат ВСЕ выбранные, не предлагается."""
        folders = self.db.get_folders()
        current = set()
        for it in selected:
            parent = it.parent()
            pnode = self._node(parent) if parent else None
            current.add(pnode["id"] if pnode and pnode["type"] == FOLDER else None)
        sub = menu.addMenu("Переместить в папку")
        for f in folders:
            if current == {f["id"]}:
                continue
            sub.addAction(f["name"],
                          lambda checked=False, fid=f["id"]:
                          self._move_leaves_to_folder(selected, fid))
        in_folder = any(c is not None for c in current)
        if in_folder:
            sub.addSeparator()
            sub.addAction("Вынести из папки",
                          lambda: self._move_leaves_to_folder(selected, None))

    def _add_create_record_menu(self, menu):
        """Подменю создания записи в выбранном контейнере: аккаунт + типы
        FIN_TYPES (пункты строятся из реестра) + сервер. Фин-типы предлагаются
        только при включённой опции «Показывать фин. инструменты», пункт
        «Сервер» — только при «Показывать серверы». Возвращает подменю."""
        sub = menu.addMenu("Создать запись")
        sub.addAction("(i) Аккаунт", self.add_account)
        if self.config.get("show_fin_instruments", False):
            for type_id, spec in FIN_TYPES.items():
                sub.addAction(spec.tree_prefix + spec.title,
                              lambda checked=False, tid=type_id: self.add_fin_record(tid))
        if self.config.get("show_servers", False):
            sub.addAction("[#] Сервер", self.add_server)
        return sub

    def _add_move_plain_to_service_menu(self, menu, selected, mover, free_label):
        """Перенос в сервис без создания нового (фин-записи, серверы)."""
        sub = menu.addMenu("Переместить в сервис")
        for s in self.db.get_services():
            sub.addAction(s["name"],
                          lambda checked=False, sid=s["id"]: mover(selected, sid))
        sub.addSeparator()
        sub.addAction(free_label, lambda: mover(selected, None))

    def _add_move_fin_to_service_menu(self, menu, selected):
        self._add_move_plain_to_service_menu(
            menu, selected, self._move_fin_items, "Сделать свободной (без сервиса)")

    def _add_move_server_to_service_menu(self, menu, selected):
        self._add_move_plain_to_service_menu(
            menu, selected, self._move_servers, "Сделать свободным (без сервиса)")
