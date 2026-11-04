"""Дерево и навигация главного окна (Эпик 4.4).

Содержит сам виджет дерева (AccountTree с ограниченным drag&drop) и примесь
TreeMixin: построение/перестроение дерева, сортировку, живой фильтр, контекстное
меню и операции над папками/сервисами/аккаунтами (создание, переименование,
перемещение, удаление). Состояние разделяется с окном через self — поведение
идентично прежнему. Подмешивается в MainWindow перед QMainWindow."""
from PySide6.QtWidgets import QTreeWidgetItem, QAbstractItemView, QMenu
from PySide6.QtCore import Qt, QSignalBlocker, QSize, QTimer
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen

from hranilka.core import domain
from hranilka.core.nodetypes import (FOLDER, SERVICE, ACCOUNT, SERVER,
                                     LEAF_TYPES, FIN_LEAF_TYPES)
from hranilka.core.fin_domain import EXPIRY_WARN_DAYS
from hranilka.core.fin_types import FIN_TYPES
from hranilka.ui import theme, pixel_icons
from hranilka.core import util
from hranilka.data.database import StaleSessionError
from hranilka.data.models import AccountData
from hranilka.ui.tree_dnd import AccountTree  # noqa: F401  (реэкспорт)
from hranilka.ui.tree_menu import TreeMenuMixin


class TreeMixin(TreeMenuMixin):
    """Построение/перестроение дерева, сортировка, поиск, контекстное меню и
    операции над папками/сервисами/аккаунтами."""

    # Префиксы-«иконки» для элементов дерева. Контейнеры/аккаунт/сервер — здесь;
    # фин-часть берётся из реестра дескрипторов (единый источник tree_prefix,
    # без дублирования).
    PREFIX = {FOLDER: "[+] ", SERVICE: "[o] ", ACCOUNT: "(i) ", SERVER: "[#] ",
              **{spec.node_type: spec.tree_prefix for spec in FIN_TYPES.values()}}

    def _node_display(self, node):
        """Текст элемента дерева с маркерами избранного, просрочки и несохранённых правок."""
        pixel = self._pixel_icons_on()
        text = ("" if pixel else self.PREFIX[node["type"]]) + node["name"]
        if node["type"] in LEAF_TYPES:
            # Избранное и метка несохранённых правок — общие для всех листьев.
            # В пиксельном режиме избранное показывает иконка-звёздочка.
            if node.get("is_favorite") and not pixel:
                text = "* " + text
            if node["type"] == ACCOUNT:
                days = node.get("pwd_days_left")
                if days is not None and days <= 0:
                    text += "  [!]"   # пора менять пароль
            elif node["type"] in FIN_LEAF_TYPES:
                days = node.get("days_until_expiry")
                if days is not None and days <= EXPIRY_WARN_DAYS:
                    text += "  [!]"   # карта истекает/истекла
            elif node["type"] == SERVER:
                days = node.get("paid_days_left")
                if days is not None and days <= 0:
                    text += "  [!]"   # оплата сервера просрочена
            if (node["type"], node["id"]) in self._dirty_ids:
                text = "● НЕ СОХРАНЕНО ▸ " + text   # есть несохранённые правки
        return text

    def _pixel_icons_on(self):
        return self.config.get("tree_icons", "text") == "pixel"

    def _node_icon(self, node):
        """Пиксельная иконка узла (звёздочка для избранного) или пустая."""
        if not self._pixel_icons_on():
            return QIcon()
        kind = (pixel_icons.FAVORITE
                if node["type"] in LEAF_TYPES and node.get("is_favorite")
                else node["type"])
        if kind not in pixel_icons.ICON_ART:
            return QIcon()
        text_color = self.config.get("text_color", "#FFFFFF")
        bg = self.config.get("tree_bg_color", "#0000AA")
        return pixel_icons.make_icon(kind, text_color, bg,
                                     self.tree.devicePixelRatioF())

    def refresh_tree_icons(self):
        """Перерисовать текст и иконки всех узлов (смена режима/цвета темы)."""
        self.tree.setIconSize(QSize(pixel_icons.SIZE, pixel_icons.SIZE))
        self.tree.setUpdatesEnabled(False)
        try:
            for it in self._iter_items():
                self._apply_item_style(it, self._node(it))
        finally:
            self.tree.setUpdatesEnabled(True)

    def _apply_item_style(self, item, node):
        """Текст + визуальное выделение для листьев с несохранёнными правками."""
        item.setText(0, self._node_display(node))
        item.setIcon(0, self._node_icon(node))
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
        """Включить DnD только для полного дерева в ручной сортировке."""
        manual = getattr(self, "sort_mode", "manual") == "manual"
        search_active = bool(getattr(self, "search_text", ""))
        enabled = manual and not search_active
        self.tree.setDragEnabled(enabled)
        self.tree.setAcceptDrops(enabled)
        self.tree.setDragDropMode(QAbstractItemView.InternalMove if enabled
                                  else QAbstractItemView.NoDragDrop)
        clear_feedback = getattr(self.tree, "clear_drop_feedback", None)
        if clear_feedback is not None:
            clear_feedback()
        if search_active:
            self.tree.setToolTip(
                "Перетаскивание недоступно во время поиска: очистите строку поиска")
        elif not manual:
            self.tree.setToolTip(
                "Для перетаскивания выберите ручную сортировку")
        else:
            self.tree.setToolTip("")

    def on_tree_drop_hint_changed(self, message):
        """Показать живую подсказку DnD, не затирая последующий статус."""
        if message:
            self._tree_drop_status_active = True
            self.statusBar().showMessage(message)
        elif getattr(self, "_tree_drop_status_active", False):
            self._tree_drop_status_active = False
            self.statusBar().clearMessage()

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

    def _tree_has_expanded_branches(self):
        """Есть ли сейчас хотя бы одна раскрытая ветка с дочерними узлами."""
        return any(it.childCount() and it.isExpanded()
                   for it in self._iter_items())

    def _tree_has_branches(self):
        """Есть ли в дереве хотя бы одна ветка, которую можно раскрыть."""
        return any(it.childCount() for it in self._iter_items())

    def on_tree_toggle_all(self):
        """Свернуть всё дерево, если раскрыта хоть одна ветка, иначе раскрыть."""
        if getattr(self, "search_text", "") or not self._tree_has_branches():
            self._update_tree_toggle_btn()
            return
        collapse = self._tree_has_expanded_branches()
        # expandAll/collapseAll испускают сигнал на каждый узел. Обновляем
        # динамическую иконку один раз после пакетной операции.
        was_blocked = self.tree.blockSignals(True)
        try:
            if collapse:
                self.tree.collapseAll()
            else:
                self.tree.expandAll()
        finally:
            self.tree.blockSignals(was_blocked)
        self._update_tree_toggle_btn()

    def _update_tree_toggle_btn(self, *_):
        """Обновить подсказку и тематическую иконку предстоящего действия."""
        btn = getattr(self, "tree_toggle_btn", None)
        tree = getattr(self, "tree", None)
        if btn is None or tree is None or getattr(self, "_building_tree", False):
            return
        search_active = bool(getattr(self, "search_text", ""))
        has_branches = self._tree_has_branches()
        collapse = has_branches and self._tree_has_expanded_branches()
        # Иконка остаётся понятной и в disabled-состоянии (пустое дерево или
        # активный поиск), а не превращается в пустую квадратную кнопку.
        btn.setIcon(self._tree_action_icon(expand=not collapse))
        btn.setEnabled(has_branches and not search_active)
        if search_active:
            btn.setToolTip("Сворачивание дерева недоступно во время поиска")
            btn.setAccessibleName(btn.toolTip())
            return
        if not has_branches:
            btn.setToolTip("В дереве нет вложенных ветвей")
            btn.setAccessibleName(btn.toolTip())
            return
        btn.setToolTip("Свернуть всё дерево" if collapse
                       else "Развернуть всё дерево")
        btn.setAccessibleName(btn.toolTip())

    def _tree_action_icon(self, expand):
        """Нарисовать иконку-иерархию: крупную для раскрытия, малую для свёртки.

        В ней намеренно нет стрелок — соседняя кнопка уже управляет направлением
        сортировки. Цвет берётся из темы и потому остаётся читаемым во всех
        пользовательских сочетаниях фона/текста.
        """
        from PySide6.QtCore import QPoint, QSize
        from PySide6.QtGui import QIcon, QPixmap

        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.config.get("text_color", "#000000"))
        painter.setPen(QPen(color, 1.7, Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(color)

        if expand:                 # крупное дерево — действие «развернуть»
            cx, top, joint, bottom, half, node = 12, 3, 12, 20, 8, 3
        else:                      # маленькое дерево — действие «свернуть»
            cx, top, joint, bottom, half, node = 12, 7, 13, 17, 5, 2

        painter.drawLine(QPoint(cx, top + node), QPoint(cx, joint))
        painter.drawLine(QPoint(cx - half, joint), QPoint(cx + half, joint))
        for x in (cx - half, cx, cx + half):
            painter.drawLine(QPoint(x, joint), QPoint(x, bottom - node))
            painter.drawRect(x - node // 2, bottom - node, node, node)
        painter.drawRect(cx - node // 2, top, node, node)
        painter.end()

        icon = QIcon(pixmap)
        btn = getattr(self, "tree_toggle_btn", None)
        if btn is not None:
            btn.setIconSize(QSize(24, 24))
        return icon

    SEARCH_DEBOUNCE_MS = 150

    def on_search_text_edited(self, text):
        """Слот для textChanged поля поиска: debounce 150 мс (P3). Синхронный
        вариант — on_search_changed."""
        self._pending_search = text
        timer = getattr(self, "_search_timer", None)
        if timer is None:
            timer = QTimer(self.tree)
            timer.setSingleShot(True)
            timer.timeout.connect(self._flush_search)
            self._search_timer = timer
        timer.start(self.SEARCH_DEBOUNCE_MS)

    def _flush_search(self):
        self.on_search_changed(getattr(self, "_pending_search", ""))

    def _expanded_keys(self):
        keys = set()
        for it in self._iter_items():
            node = self._node(it)
            if node and it.isExpanded():
                keys.add((node.get("type"), node.get("id")))
        return keys

    def on_search_changed(self, text):
        timer = getattr(self, "_search_timer", None)
        if timer is not None:
            timer.stop()              # синхронный вызов отменяет отложенный
        self.search_text = text.strip().lower()
        snapshot = getattr(self, "_pre_search_expanded", None)
        if self.search_text and snapshot is None:
            # B5: запоминаем раскрытие до первого раскрытия поиском.
            self._pre_search_expanded = self._expanded_keys()
        self._apply_filter()
        if not self.search_text and snapshot is not None:
            self._pre_search_expanded = None
            self._restore_expanded(snapshot)
        self._update_dnd_mode()
        self._update_tree_toggle_btn()

    def _restore_expanded(self, keys):
        """Вернуть раскрытие папок/сервисов по снимку (type, id)."""
        self.tree.setUpdatesEnabled(False)
        try:
            for it in self._iter_items():
                node = self._node(it)
                if node and node.get("type") in (FOLDER, SERVICE):
                    it.setExpanded((node.get("type"), node.get("id")) in keys)
        finally:
            self.tree.setUpdatesEnabled(True)

    def _apply_filter(self):
        self.tree.setUpdatesEnabled(False)
        try:
            self._apply_filter_inner()
        finally:
            self.tree.setUpdatesEnabled(True)

    def _apply_filter_inner(self):
        text = self.search_text

        def visit(item):
            node = self._node(item)
            last4 = node.get("card_last4") or ""     # поиск карты по «•1234»
            name_lower = node.get("name_lower")
            if name_lower is None:
                name_lower = node["name"].lower()
            self_match = ((not text) or (text in name_lower)
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

    def on_tree_order_changed(self, parent_item, group):
        """Сохраняет новый порядок после перетаскивания (async — запись не виснет
        на UI, H-8). Синхронно снимает id только изменённой логической группы;
        запись выполняется фоновым воркером БД."""
        allowed_groups = {
            None: {FOLDER, SERVICE, ACCOUNT, AccountTree.FIN_GROUP, SERVER},
            FOLDER: {SERVICE, ACCOUNT, AccountTree.FIN_GROUP, SERVER},
            SERVICE: {ACCOUNT, AccountTree.FIN_GROUP, SERVER},
        }
        parent_type = (self._node(parent_item)["type"]
                       if parent_item is not None else None)
        if group not in allowed_groups.get(parent_type, set()):
            return

        if parent_item is None:
            items = [self.tree.topLevelItem(i)
                     for i in range(self.tree.topLevelItemCount())]
        else:
            items = [parent_item.child(i)
                     for i in range(parent_item.childCount())]
        ids = [self._node(item)["id"] for item in items
               if AccountTree.item_order_group(item) == group]
        method_by_group = {
            FOLDER: self.db.set_folders_order,
            SERVICE: self.db.set_services_order,
            ACCOUNT: self.db.set_accounts_order,
            AccountTree.FIN_GROUP: self.db.set_fin_items_order,
            SERVER: self.db.set_servers_order,
        }
        util.fire(self._save_order_async([(method_by_group[group], ids)]))

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
            # Ручная перестановка уже видна в UI. При сбое возвращаем дерево к
            # фактически сохранённому состоянию, чтобы следующий drag не писал
            # порядок поверх ошибочного представления.
            self._reload_tree()
            return
        self._any_db_changes = True
        self.statusBar().showMessage("Порядок сохранён", 2000)

    # ----- Построение дерева -----

    def populate_tree(self):
        # Отключаем перерисовку на время массовой вставки: дерево не
        # перерисовывается на каждый добавленный узел, а один раз в конце —
        # заметно быстрее на больших базах (ускорение запуска).
        self._building_tree = True
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.clear()
            include_fin = self.config.get("show_fin_instruments", False)
            include_servers = self.config.get("show_servers", False)
            for node in self.db.get_tree_structure(
                    self.sort_mode, self.sort_desc, include_fin=include_fin,
                    include_servers=include_servers):
                self._add_tree_node(self.tree, node)
        finally:
            self.tree.setUpdatesEnabled(True)
            self._building_tree = False
        self._apply_filter()
        self._update_tree_toggle_btn()

    def _add_tree_node(self, parent, node):
        data = {k: v for k, v in node.items() if k != "children"}
        data["name_lower"] = str(node["name"]).lower()   # для быстрого поиска (P3)
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
        expanded = set()
        for it in self._iter_items():          # один проход (P2)
            if it.isExpanded():
                node = self._node(it)
                expanded.add((node["type"], node["id"]))
        sel = self._node(self.tree.currentItem())
        sel_key = (sel["type"], sel["id"]) if sel else None

        blocker = QSignalBlocker(self.tree)   # B3: снимается и при исключении
        try:
            self.populate_tree()
            found = None
            for it in self._iter_items():      # второй (и последний) проход
                node = self._node(it)
                key = (node["type"], node["id"])
                if node["type"] in (FOLDER, SERVICE):
                    it.setExpanded(key in expanded)
                if sel_key and key == sel_key:
                    found = it
            if found is not None:
                self.tree.setCurrentItem(found)
            # B4: ссылка на уничтоженный при clear() элемент не должна жить.
            self.current_tree_item = found
            if self.search_text:               # раскрытие совпадений поиска
                self._apply_filter()
        finally:
            blocker.unblock()
        self._update_tree_toggle_btn()

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
            "name_lower": str(new_name).lower(),
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
                # Лист/сервис внутри свёрнутой папки/сервиса: раскрываем
                # предков, иначе выбранный узел остаётся невидимым.
                parent = it.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self.tree.setCurrentItem(it)
                return

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
            elif node["type"] == SERVER:
                method = self.db.rename_server
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
        # M-01: сервер сбрасываем симметрично аккаунту/фин-записи — иначе
        # после удаления открытого сервера (или контейнера с ним)
        # _current_server/current_server_data продолжают указывать на уже
        # удалённую запись, хотя правая панель уже заменена на заглушку.
        self._current_server = None
        self.current_server_data = None
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

    def _make_menu(self):
        return QMenu(self)

    # Таблица «вид узла -> типы узлов и методы БД». Методы берутся из self.db в
    # момент вызова (getattr), одна транзакция БД на пакетную операцию (M7-04).
    _OP_KINDS = {
        "account": ((ACCOUNT,), "set_favorites", "move_accounts"),
        "fin": (tuple(FIN_LEAF_TYPES), "set_fin_favorites", "move_fin_items"),
        "server": ((SERVER,), "set_server_favorites", "move_servers"),
        "service": ((SERVICE,), None, "move_services"),
    }

    def _selected_ids(self, kind, selected):
        """id выбранных узлов нужного вида (account/fin/server/service)."""
        types = self._OP_KINDS[kind][0]
        return [n["id"] for n in (self._node(i) for i in selected)
                if n["type"] in types]

    def set_favorite_selected(self, kind, selected, value):
        method = getattr(self.db, self._OP_KINDS[kind][1])
        util.fire(self._run_then_reload(
            method, (self._selected_ids(kind, selected), value),
            "Не удалось выполнить операцию"))

    def move_selected(self, kind, selected, target_id):
        method = getattr(self.db, self._OP_KINDS[kind][2])
        util.fire(self._run_then_reload(
            method, (self._selected_ids(kind, selected), target_id),
            "Не удалось переместить", status="Перемещено"))

    # Тонкие обёртки со старыми именами (используются меню и внешним кодом).
    def _set_favorite(self, selected, value):
        self.set_favorite_selected("account", selected, value)

    def _fin_ids(self, selected):
        return self._selected_ids("fin", selected)

    def _set_fin_favorite(self, selected, value):
        self.set_favorite_selected("fin", selected, value)

    def _move_fin_items(self, selected, service_id):
        self.move_selected("fin", selected, service_id)

    def _server_ids(self, selected):
        return self._selected_ids("server", selected)

    def _set_server_favorite(self, selected, value):
        self.set_favorite_selected("server", selected, value)

    def _move_servers(self, selected, service_id):
        self.move_selected("server", selected, service_id)

    def _find_leaf_item(self, node_type, node_id):
        """Элемент дерева листа (аккаунт/карта/кошелёк) по (типу, id) или None."""
        for it in self._iter_items():
            node = self._node(it)
            if node and node["type"] == node_type and node["id"] == node_id:
                return it
        return None

    def _leaf_keys(self, selected):
        """[(type, id)] выбранных листьев (аккаунт/карта/кошелёк/сервер)."""
        return [(n["type"], n["id"]) for n in (self._node(i) for i in selected)
                if n and n["type"] in LEAF_TYPES]

    def _move_leaves_to_folder(self, selected, folder_id):
        """Перенос выбранных листьев прямо в папку (folder_id=None - в корень)
        ОДНОЙ транзакцией БД. Целевая папка раскрывается после перестроения."""
        keys = self._leaf_keys(selected)
        if not keys:
            return
        util.fire(self._move_leaves_async(keys, folder_id))

    async def _move_leaves_async(self, keys, folder_id):
        await self._run_then_reload(
            self.db.move_leaves, (keys, None, folder_id),
            "Не удалось переместить", status="Перемещено")
        if folder_id is not None:
            for it in self._iter_items():
                n = self._node(it)
                if n and n["type"] == FOLDER and n["id"] == folder_id:
                    it.setExpanded(True)
                    break

    def on_tree_reparent(self, items, target_item):
        """Drop листьев на папку (сигнал AccountTree.reparent_requested)."""
        target = self._node(target_item)
        if target and target["type"] == FOLDER:
            self._move_leaves_to_folder(items, target["id"])

    def _move_services(self, selected, folder_id):
        self.move_selected("service", selected, folder_id)

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
        self.move_selected("account", selected, service_id)

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

    def _new_leaf_parent(self):
        """Родитель нового листа по текущему выбору: (service_id, folder_id).
        Сервис - в сервис; папка - прямо в папку; лист - к тому же родителю
        (сервис/папка/корень); ничего не выбрано - в корень (None, None)."""
        current = self.tree.currentItem()
        node = self._node(current)
        if node and node["type"] == SERVICE:
            return node["id"], None
        if node and node["type"] == FOLDER:
            return None, node["id"]
        if node and node["type"] in LEAF_TYPES:
            parent = current.parent()
            pnode = self._node(parent) if parent else None
            if pnode and pnode["type"] == SERVICE:
                return pnode["id"], None
            if pnode and pnode["type"] == FOLDER:
                return None, pnode["id"]
        return None, None

    def add_account(self):
        service_id, folder_id = self._new_leaf_parent()

        name, ok = theme.themed_input(self.config, self, "Новый аккаунт", "Название:")
        name = name.strip()
        if ok and name:
            data = AccountData()
            data.name = name
            # add_account + save_account (поля по умолчанию) + выбор узла — в одной
            # coroutine: save зависит от id, а выбор — от завершённой записи (H-8).
            util.fire(self._add_account_async(
                service_id, name, data.to_storage(), folder_id))

    async def _add_account_async(self, service_id, name, storage, folder_id=None):
        session = self.db.current_session()
        # Создание аккаунта и запись его карточки — ОДНА транзакция БД (M7-04):
        # раньше два коммита оставляли полупустой аккаунт при сбое второго.
        try:
            account_id = await self.db.run_async(
                self.db.add_account_with_card, service_id, name, storage,
                folder_id, _session=session)
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
        service_id, folder_id = self._new_leaf_parent()

        name, ok = theme.themed_input(
            self.config, self, spec.create_title, "Название:")
        name = name.strip()
        if ok and name:
            util.fire(self._add_fin_record_async(
                service_id, type_id, name, folder_id))

    async def _add_fin_record_async(self, service_id, type_id, name,
                                    folder_id=None):
        session = self.db.current_session()
        try:
            item_id = await self.db.run_async(
                self.db.add_fin_item, service_id, type_id, name, folder_id,
                _session=session)
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

    def add_server(self):
        """Создание VPS-сервера (docs/ТЗ_VPS_Серверы.md §4). Флоу как у
        аккаунта/фин-записи: контейнер (выбранный сервис или корень) → имя →
        пустая запись в БД → выбор узла → карточка сразу в правке."""
        service_id, folder_id = self._new_leaf_parent()

        name, ok = theme.themed_input(
            self.config, self, "Новый сервер", "Название:")
        name = name.strip()
        if ok and name:
            util.fire(self._add_server_async(service_id, name, folder_id))

    async def _add_server_async(self, service_id, name, folder_id=None):
        session = self.db.current_session()
        try:
            server_id = await self.db.run_async(
                self.db.add_server, service_id, name, folder_id,
                _session=session)
        except StaleSessionError:
            return
        except Exception as e:                       # noqa: BLE001
            self._show_card_error("Не удалось создать сервер", e)
            self._reload_tree()
            return
        self._any_db_changes = True
        self._reload_tree()
        # Новый сервер сразу в режим правки после загрузки (одноразовый флаг).
        self._server_edit_on_load = server_id
        self._select_node(SERVER, server_id)
