"""Дерево и навигация главного окна (Эпик 4.4).

Содержит сам виджет дерева (AccountTree с ограниченным drag&drop) и примесь
TreeMixin: построение/перестроение дерева, сортировку, живой фильтр, контекстное
меню и операции над папками/сервисами/аккаунтами (создание, переименование,
перемещение, удаление). Состояние разделяется с окном через self — поведение
идентично прежнему. Подмешивается в MainWindow перед QMainWindow."""
from PySide6.QtWidgets import (QTreeWidget, QTreeWidgetItem, QMenu,
                               QAbstractItemView)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush

from hranilka.core import domain
from hranilka.core.nodetypes import (FOLDER, SERVICE, ACCOUNT,
                                     LEAF_TYPES, FIN_LEAF_TYPES)
from hranilka.core.fin_domain import EXPIRY_WARN_DAYS
from hranilka.core.fin_types import FIN_TYPES
from hranilka.ui import theme
from hranilka.core import util
from hranilka.data.database import StaleSessionError
from hranilka.data.models import AccountData


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

    # Префиксы-«иконки» для элементов дерева. Контейнеры/аккаунт — здесь; фин-часть
    # берётся из реестра дескрипторов (единый источник tree_prefix, без дублирования).
    PREFIX = {FOLDER: "[+] ", SERVICE: "[o] ", ACCOUNT: "(i) ",
              **{spec.node_type: spec.tree_prefix for spec in FIN_TYPES.values()}}

    def _node_display(self, node):
        """Текст элемента дерева с маркерами избранного, просрочки и несохранённых правок."""
        text = self.PREFIX[node["type"]] + node["name"]
        if node["type"] in LEAF_TYPES:
            # Избранное и метка несохранённых правок — общие для всех листьев.
            if node.get("is_favorite"):
                text = "* " + text
            if node["type"] == ACCOUNT:
                days = node.get("pwd_days_left")
                if days is not None and days <= 0:
                    text += "  [!]"   # пора менять пароль
            elif node["type"] in FIN_LEAF_TYPES:
                days = node.get("days_until_expiry")
                if days is not None and days <= EXPIRY_WARN_DAYS:
                    text += "  [!]"   # карта истекает/истекла
            if (node["type"], node["id"]) in self._dirty_ids:
                text = "● НЕ СОХРАНЕНО ▸ " + text   # есть несохранённые правки
        return text

    def _apply_item_style(self, item, node):
        """Текст + визуальное выделение для листьев с несохранёнными правками."""
        item.setText(0, self._node_display(node))
        dirty = (node["type"], node["id"]) in self._dirty_ids
        font = item.font(0)
        font.setBold(dirty)
        item.setFont(0, font)
        # Маркер «не сохранено» — только префикс «● НЕ СОХРАНЕНО ▸» и жирный
        # шрифт. Цвета текста/фона НЕ переопределяем: элемент рисуется цветами
        # темы из настроек (как остальные), а выделение — правилами
        # item:selected из QSS. Жёсткая пара чёрный-на-жёлтом ломала темизацию
        # невыбранного элемента (текст был чёрным на любой теме).
        item.setForeground(0, QBrush())
        item.setBackground(0, QBrush())

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
            last4 = node.get("card_last4") or ""     # поиск карты по «•1234»
            self_match = ((not text) or (text in node["name"].lower())
                          or (text in last4))
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
        """Сохраняет новый порядок после перетаскивания (async — запись не виснет
        на UI, H-8). Порядок id снимаем СИНХРОННО сейчас (снимок текущего дерева),
        а сами записи выполняем в фоновом потоке БД. Для корня (parent_item is None)
        порядок сохраняется по каждой группе типов отдельно (папки / корневые
        сервисы / корневые аккаунты), т.к. они в разных таблицах со своим
        sort_order. Единый воркер БД сохраняет порядок вызовов (a)."""
        if parent_item is None:
            folder_ids, service_ids, account_ids, fin_ids = [], [], [], []
            for i in range(self.tree.topLevelItemCount()):
                node = self._node(self.tree.topLevelItem(i))
                if node["type"] == FOLDER:
                    folder_ids.append(node["id"])
                elif node["type"] == SERVICE:
                    service_ids.append(node["id"])
                elif node["type"] == ACCOUNT:
                    account_ids.append(node["id"])
                elif node["type"] in FIN_LEAF_TYPES:
                    fin_ids.append(node["id"])
            util.fire(self._save_order_async(
                [(self.db.set_folders_order, folder_ids),
                 (self.db.set_services_order, service_ids),
                 (self.db.set_accounts_order, account_ids),
                 (self.db.set_fin_items_order, fin_ids)]))
            return

        parent_node = self._node(parent_item)
        if parent_node["type"] == FOLDER:
            service_ids = [self._node(parent_item.child(i))["id"]
                           for i in range(parent_item.childCount())]
            ops = [(self.db.set_services_order, service_ids)]
        elif parent_node["type"] == SERVICE:
            # Аккаунты и fin-записи — братья одного сервиса, но в разных таблицах;
            # порядок каждой группы снимаем в порядке их появления в дереве.
            account_ids, fin_ids = [], []
            for i in range(parent_item.childCount()):
                cn = self._node(parent_item.child(i))
                if cn["type"] == ACCOUNT:
                    account_ids.append(cn["id"])
                elif cn["type"] in FIN_LEAF_TYPES:
                    fin_ids.append(cn["id"])
            ops = [(self.db.set_accounts_order, account_ids),
                   (self.db.set_fin_items_order, fin_ids)]
        else:
            return
        util.fire(self._save_order_async(ops))

    async def _save_order_async(self, ops):
        """Записать порядок (список (метод, ids)) в фоновом потоке БД, сохраняя
        порядок вызовов. Токен сессии фиксируем один раз: смена сессии
        (lock/restore) прерывает запись (StaleSessionError — молча)."""
        session = self.db.current_session()
        try:
            for method, ids in ops:
                await self.db.run_async(method, ids, _session=session)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            self._show_card_error("Не удалось сохранить порядок", e)
            return
        self.statusBar().showMessage("Порядок сохранён", 2000)

    # ----- Построение дерева -----

    def populate_tree(self):
        # Отключаем перерисовку на время массовой вставки: дерево не
        # перерисовывается на каждый добавленный узел, а один раз в конце —
        # заметно быстрее на больших базах (ускорение запуска).
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.clear()
            include_fin = self.config.get("show_fin_instruments", False)
            for node in self.db.get_tree_structure(
                    self.sort_mode, self.sort_desc, include_fin=include_fin):
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
        if node["type"] in (FOLDER, SERVICE):
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
            if node["type"] in (FOLDER, SERVICE):
                it.setExpanded((node["type"], node["id"]) in expanded)
            if sel_key and (node["type"], node["id"]) == sel_key:
                self.tree.setCurrentItem(it)
                self.current_tree_item = it
        self.tree.blockSignals(False)

    # ----- Точечное обновление узла (без перестройки всего дерева) -----

    def _refresh_account_item(self, account_id):
        """Точечно перерисовать узел аккаунта (текст/жирность/маркеры) по его
        текущему node-словарю. True — элемент найден и обновлён; False —
        вызывающий должен сделать fallback на _reload_tree()."""
        item = self._find_leaf_item(ACCOUNT, account_id)
        if item is None:
            return False
        self._apply_item_style(item, self._node(item))
        return True

    def _update_account_item_after_save(self, account_id, fields):
        """Обновить узел аккаунта после сохранения карточки без полной
        перестройки дерева. fields — storage["fields"] сохранённой карточки.

        Полная перестройка (_reload_tree) выполняется только когда сохранение
        могло изменить ПОРЯДОК элементов — сортировка по имени/сроку пароля с
        изменившимся ключом — либо элемент не найден (безопасный fallback)."""
        item = self._find_leaf_item(ACCOUNT, account_id)
        if item is None:
            self._reload_tree()
            return
        node = self._node(item)
        new_name = fields["account_name"]
        new_days = domain.days_until_password_change(
            fields["password_changed_date"],
            fields["password_change_interval_days"])
        name_changed = node["name"] != new_name
        order_changed = (
            (self.sort_mode == "name" and name_changed)
            or (self.sort_mode == "pwd_due"
                and node.get("pwd_days_left") != new_days))
        node.update({
            "name": new_name,
            "login": fields["login"],
            "password_changed_date": fields["password_changed_date"],
            "password_change_interval_days": fields["password_change_interval_days"],
            "pwd_days_left": new_days,
        })
        item.setData(0, Qt.UserRole, node)
        item.setToolTip(0, new_name)
        self._apply_item_style(item, node)
        if order_changed:
            self._reload_tree()
        elif name_changed and self.search_text:
            self._apply_filter()   # видимость под активным поиском зависит от имени

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
            if t == ACCOUNT:
                self._add_move_to_service_menu(menu, selected)
                fav = node.get("is_favorite")
                menu.addAction("Убрать из избранного" if fav else "В избранное",
                               lambda: self._set_favorite(selected, not fav))
                menu.addSeparator()
                menu.addAction("Экспорт…", lambda: self.open_export(node))
                menu.addAction("Удалить", lambda: self.delete_items(selected))
            elif t in FIN_LEAF_TYPES:
                self._add_move_fin_to_service_menu(menu, selected)
                fav = node.get("is_favorite")
                menu.addAction("Убрать из избранного" if fav else "В избранное",
                               lambda: self._set_fin_favorite(selected, not fav))
                menu.addSeparator()
                menu.addAction("Экспорт…", lambda: self.open_export(node))
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
            if types == {SERVICE}:
                self._add_move_to_folder_menu(menu, selected)
                self._add_delete_menu(menu, selected, with_keep=True)
            elif types == {ACCOUNT}:
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

    def _add_create_record_menu(self, menu):
        """Подменю создания записи в выбранном контейнере: аккаунт + типы
        FIN_TYPES (пункты строятся из реестра). Фин-типы предлагаются только при
        включённой опции «Показывать фин. инструменты». Возвращает подменю."""
        sub = menu.addMenu("Создать запись")
        sub.addAction("(i) Аккаунт", self.add_account)
        if not self.config.get("show_fin_instruments", False):
            return sub
        for type_id, spec in FIN_TYPES.items():
            sub.addAction(spec.tree_prefix + spec.title,
                          lambda checked=False, tid=type_id: self.add_fin_record(tid))
        return sub

    def _add_move_fin_to_service_menu(self, menu, selected):
        """Перемещение фин-записей между сервисами (по образцу аккаунтного меню,
        но без создания нового сервиса — фин-записи туда переносят реже)."""
        sub = menu.addMenu("Переместить в сервис")
        for s in self.db.get_services():
            sub.addAction(s["name"],
                          lambda checked=False, sid=s["id"]: self._move_fin_items(selected, sid))
        sub.addSeparator()
        sub.addAction("Сделать свободной (без сервиса)",
                      lambda: self._move_fin_items(selected, None))

    # ----- Операции меню -----

    def rename_item(self, item):
        node = self._node(item)
        new_name, ok = theme.themed_input(
            self.config, self, "Переименование", "Новое название:", node["name"]
        )
        if ok and new_name:
            if node["type"] == FOLDER:
                method = self.db.rename_folder
            elif node["type"] == SERVICE:
                method = self.db.rename_service
            else:
                return
            # Запись в фоне, дерево перестраиваем ТОЛЬКО после завершения (H-8).
            util.fire(self._db_write_then_reload(
                [(method, (node["id"], new_name))]))

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
        # Удаление (вкл. чтение потомков) — в фоновом потоке БД; UI обновляем
        # только после завершения (H-8). Снимок узлов снят синхронно (nodes).
        util.fire(self._delete_items_async(nodes, keep, to_bin))

    async def _delete_items_async(self, nodes, keep, to_bin):
        session = self.db.current_session()
        # Всё удаление — ОДНА транзакция БД (M7-04): либо весь набор удалён, либо
        # ничего. Кэш несохранённых правок чистим ТОЛЬКО после успеха, по списку
        # реально удалённых/перемещённых в корзину аккаунтов, что возвращает БД.
        items = [(node["type"], node["id"]) for node in nodes]
        try:
            affected = await self.db.run_async(
                self.db.delete_items, items, keep, to_bin, _session=session)
        except StaleSessionError:
            return                               # БД сменена (lock/restore) — молча
        except Exception as e:                       # noqa: BLE001
            self._show_card_error("Не удалось удалить", e)
            self._reload_tree()                  # операция атомарна — ресинк UI (страховка)
            return
        self._forget_leaf_cache(nodes, affected)
        self._any_db_changes = True

        self.current_tree_item = None
        self._current_account_id = None
        self._current_fin = None
        self.is_editing = False
        self._reload_tree()
        self._show_placeholder()
        self._update_bin_button()
        self.statusBar().showMessage("Удалено", 2000)

    def _forget_leaf_cache(self, nodes, affected):
        """Убирает несохранённые правки удалённых листьев из памяти. Выбранные
        листья (аккаунты/карты) забываются по точному ключу (type, id); остальные
        affected — типизированные потомки удалённых контейнеров. Точность важна:
        id аккаунта и карты могут совпадать."""
        keys = set(affected)
        for n in nodes:
            if n["type"] in LEAF_TYPES:
                key = (n["type"], n["id"])
                keys.add(key)
        for key in keys:
            self._edit_cache.pop(key, None)
            self._dirty_ids.discard(key)

    async def _db_write_then_reload(self, ops, status=None):
        """Выполнить список DB-операций (метод, args-кортеж) в фоновом потоке БД,
        затем перестроить дерево — но ТОЛЬКО после завершения записи (H-8: UI не
        должен опережать запись). Порядок вызовов сохраняется (единый воркер БД).
        Токен сессии фиксируем один раз — смена сессии прерывает всю операцию."""
        session = self.db.current_session()
        try:
            for method, args in ops:
                await self.db.run_async(method, *args, _session=session)
        except StaleSessionError:
            return                               # БД сменена (lock/restore) — молча
        except Exception as e:                       # noqa: BLE001
            self._show_card_error("Не удалось выполнить операцию", e)
            self._reload_tree()                  # ресинк UI на случай сбоя (страховка)
            return
        self._any_db_changes = True
        self._reload_tree()
        if status:
            self.statusBar().showMessage(status, 2000)

    async def _run_then_reload(self, method, args, err_title, status=None):
        """Выполнить ОДИН составной DB-метод в фоне и перестроить дерево после
        завершения (H-8). Составные методы атомарны (M7-04): при сбое БД не
        меняется, поэтому на ошибке дерево лишь ресинкается (дешёвая страховка)."""
        session = self.db.current_session()
        try:
            await self.db.run_async(method, *args, _session=session)
        except StaleSessionError:
            return                               # БД сменена (lock/restore) — молча
        except Exception as e:                       # noqa: BLE001
            self._show_card_error(err_title, e)
            self._reload_tree()
            return
        self._any_db_changes = True
        self._reload_tree()
        if status:
            self.statusBar().showMessage(status, 2000)

    def _set_favorite(self, selected, value):
        ids = [node["id"] for node in (self._node(i) for i in selected)
               if node["type"] == ACCOUNT]
        util.fire(self._run_then_reload(
            self.db.set_favorites, (ids, value), "Не удалось выполнить операцию"))

    def _fin_ids(self, selected):
        """id выбранных фин-записей (карты/кошельки)."""
        return [n["id"] for n in (self._node(i) for i in selected)
                if n["type"] in FIN_LEAF_TYPES]

    def _set_fin_favorite(self, selected, value):
        ids = self._fin_ids(selected)
        # Пакетный метод — ОДНА транзакция БД (M7-04), как у аккаунтов
        # (_set_favorite/set_favorites): раньше это был цикл с транзакцией на
        # запись, и сбой середины списка оставлял половину выбора без отката.
        util.fire(self._run_then_reload(
            self.db.set_fin_favorites, (ids, value), "Не удалось выполнить операцию"))

    def _move_fin_items(self, selected, service_id):
        ids = self._fin_ids(selected)
        util.fire(self._run_then_reload(
            self.db.move_fin_items, (ids, service_id),
            "Не удалось переместить", status="Перемещено"))

    def _find_leaf_item(self, node_type, node_id):
        """Элемент дерева листа (аккаунт/карта/кошелёк) по (типу, id) или None."""
        for it in self._iter_items():
            node = self._node(it)
            if node and node["type"] == node_type and node["id"] == node_id:
                return it
        return None

    def _move_services(self, selected, folder_id):
        ids = [node["id"] for node in (self._node(i) for i in selected)
               if node["type"] == SERVICE]
        util.fire(self._run_then_reload(
            self.db.move_services, (ids, folder_id),
            "Не удалось переместить", status="Перемещено"))

    def _move_services_new_folder(self, selected):
        name, ok = theme.themed_input(self.config, self, "Новая папка", "Название:")
        if ok and name:
            # Создание папки и перенос — ОДНА транзакция БД (M7-04).
            svc_ids = [self._node(i)["id"] for i in selected
                       if self._node(i)["type"] == SERVICE]
            util.fire(self._run_then_reload(
                self.db.move_services_to_new_folder, (name, svc_ids),
                "Не удалось переместить в папку", status="Перемещено"))

    def _move_accounts(self, selected, service_id):
        ids = [node["id"] for node in (self._node(i) for i in selected)
               if node["type"] == ACCOUNT]
        util.fire(self._run_then_reload(
            self.db.move_accounts, (ids, service_id),
            "Не удалось переместить", status="Перемещено"))

    def _move_accounts_new_service(self, selected):
        name, ok = theme.themed_input(self.config, self, "Новый сервис", "Название:")
        if ok and name:
            acc_ids = [self._node(i)["id"] for i in selected
                       if self._node(i)["type"] == ACCOUNT]
            # Создание сервиса и перенос — ОДНА транзакция БД (M7-04).
            util.fire(self._run_then_reload(
                self.db.move_accounts_to_new_service, (name, acc_ids),
                "Не удалось переместить в сервис", status="Перемещено"))

    # ----- Добавление элементов -----

    def add_folder(self):
        name, ok = theme.themed_input(self.config, self, "Новая папка", "Название:")
        if ok and name:
            # Запись + выбор нового узла — в одной coroutine: выбор зависит от
            # возвращённого id, поэтому write и select идут последовательно (H-8).
            util.fire(self._add_node_then_select(
                self.db.add_folder, (name,), FOLDER))

    def add_service(self):
        # Сервис можно добавить в выбранную папку либо как самостоятельный
        # (вне папки) — оба варианта допускаются ТЗ.
        current = self.tree.currentItem()
        node = self._node(current)
        folder_id = node["id"] if node and node["type"] == FOLDER else None

        name, ok = theme.themed_input(self.config, self, "Новый сервис", "Название:")
        if ok and name:
            util.fire(self._add_node_then_select(
                self.db.add_service, (name, folder_id), SERVICE))

    async def _add_node_then_select(self, method, args, node_type):
        """Создать узел (метод возвращает новый id) в фоне, затем перестроить
        дерево и выбрать созданный узел — строго после завершения записи (H-8)."""
        session = self.db.current_session()
        try:
            new_id = await self.db.run_async(method, *args, _session=session)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            self._show_card_error("Не удалось создать элемент", e)
            self._reload_tree()                  # ресинк UI на случай сбоя (страховка)
            return
        self._any_db_changes = True
        self._reload_tree()
        self._select_node(node_type, new_id)

    def add_account(self):
        current = self.tree.currentItem()
        node = self._node(current)
        if node and node["type"] == SERVICE:
            service_id = node["id"]
        elif node and node["type"] == ACCOUNT:
            # Аккаунт под сервисом → тот же сервис; свободный аккаунт → корень.
            parent = current.parent()
            pnode = self._node(parent) if parent else None
            service_id = pnode["id"] if pnode and pnode["type"] == SERVICE else None
        else:
            # Папка или ничего не выбрано → свободный аккаунт (в корне).
            service_id = None

        name, ok = theme.themed_input(self.config, self, "Новый аккаунт", "Название:")
        if ok and name:
            data = AccountData()
            data.name = name
            # add_account + save_account (поля по умолчанию) + выбор узла — в одной
            # coroutine: save зависит от id, а выбор — от завершённой записи (H-8).
            util.fire(self._add_account_async(service_id, name, data.to_storage()))

    async def _add_account_async(self, service_id, name, storage):
        session = self.db.current_session()
        # Создание аккаунта и запись его карточки — ОДНА транзакция БД (M7-04):
        # раньше два коммита оставляли полупустой аккаунт при сбое второго.
        try:
            account_id = await self.db.run_async(
                self.db.add_account_with_card, service_id, name, storage,
                _session=session)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            self._show_card_error("Не удалось создать аккаунт", e)
            self._reload_tree()                  # ресинк UI на случай сбоя (страховка)
            return
        self._any_db_changes = True
        self._reload_tree()
        # Новый аккаунт сразу открываем в режиме правки: карточка уже записана
        # в БД (add_account_with_card, одна транзакция), поэтому включение
        # правки — чисто UI-переключение после завершения async-загрузки.
        self._edit_on_load_id = account_id
        self._select_node(ACCOUNT, account_id)

    def add_fin_record(self, type_id):
        """Создание финансовой записи (карта/кошелёк) выбранного типа. Флоу как у
        аккаунта: имя → пустая запись в БД → выбор узла → карточка в правке."""
        spec = FIN_TYPES.get(type_id)
        if spec is None:
            return
        current = self.tree.currentItem()
        node = self._node(current)
        if node and node["type"] == SERVICE:
            service_id = node["id"]
        elif node and node["type"] in LEAF_TYPES:
            # Лист под сервисом → тот же сервис; свободный → корень.
            parent = current.parent()
            pnode = self._node(parent) if parent else None
            service_id = pnode["id"] if pnode and pnode["type"] == SERVICE else None
        else:
            # Папка или ничего не выбрано → свободная запись (в корне).
            service_id = None

        name, ok = theme.themed_input(
            self.config, self, spec.create_title, "Название:")
        if ok and name:
            util.fire(self._add_fin_record_async(service_id, type_id, name))

    async def _add_fin_record_async(self, service_id, type_id, name):
        session = self.db.current_session()
        try:
            item_id = await self.db.run_async(
                self.db.add_fin_item, service_id, type_id, name, _session=session)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            self._show_card_error("Не удалось создать запись", e)
            self._reload_tree()
            return
        self._any_db_changes = True
        self._reload_tree()
        spec = FIN_TYPES[type_id]
        # Новая запись сразу в режим правки после загрузки (одноразовый флаг).
        self._fin_edit_on_load = (spec.node_type, item_id)
        self._select_node(spec.node_type, item_id)
