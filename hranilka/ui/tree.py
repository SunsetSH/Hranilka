"""Дерево и навигация главного окна (Эпик 4.4).

Содержит сам виджет дерева (AccountTree с ограниченным drag&drop) и примесь
TreeMixin: построение/перестроение дерева, сортировку, живой фильтр, контекстное
меню и операции над папками/сервисами/аккаунтами (создание, переименование,
перемещение, удаление). Состояние разделяется с окном через self — поведение
идентично прежнему. Подмешивается в MainWindow перед QMainWindow."""
from PySide6.QtWidgets import (QTreeWidget, QTreeWidgetItem, QMenu,
                               QAbstractItemView)
from PySide6.QtCore import Qt, Signal, QItemSelectionModel
from PySide6.QtGui import QBrush, QColor, QCursor, QDrag, QPainter, QPen

from hranilka.core import domain
from hranilka.core.nodetypes import (FOLDER, SERVICE, ACCOUNT, SERVER,
                                     LEAF_TYPES, FIN_LEAF_TYPES)
from hranilka.core.fin_domain import EXPIRY_WARN_DAYS
from hranilka.core.fin_types import FIN_TYPES
from hranilka.ui import theme
from hranilka.core import util
from hranilka.data.database import StaleSessionError
from hranilka.data.models import AccountData


class AccountTree(QTreeWidget):
    """Дерево с безопасным переупорядочиванием внутри логической группы.

    Вся верхняя половина строки означает вставку перед ней, нижняя — после.
    Перенос между родителями остаётся в контекстном меню. Карты и кошельки
    считаются одной группой, потому что хранят общий ``sort_order``.
    """

    FIN_GROUP = "fin_items"

    # Параметры: элемент-родитель (или None для корня) и логическая группа.
    order_changed = Signal(object, str)
    drop_rejected = Signal()
    drop_hint_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drop_line_y = None
        self._drop_hint = ""
        self._drop_indicator_color = self.palette().text().color()

    @staticmethod
    def _item_type(item):
        if item is None:
            return None
        data = item.data(0, Qt.UserRole)
        return data["type"] if data else None

    @classmethod
    def item_order_group(cls, item):
        """Группа с единым ``sort_order`` в БД."""
        item_type = cls._item_type(item)
        return cls.FIN_GROUP if item_type in FIN_LEAF_TYPES else item_type

    @staticmethod
    def _favorite_bucket(item):
        data = item.data(0, Qt.UserRole) if item is not None else None
        return bool(data and data.get("is_favorite"))

    @staticmethod
    def _item_name(item):
        data = item.data(0, Qt.UserRole) if item is not None else None
        return str(data.get("name", "")) if data else ""

    def set_drop_indicator_color(self, color):
        """Подстроить индикатор вставки под текущую тему."""
        themed = QColor(color)
        self._drop_indicator_color = (
            themed if themed.isValid() else self.palette().text().color())
        if self._drop_line_y is not None:
            self.viewport().update()

    def clear_drop_feedback(self):
        """Убрать линию вставки и подсказку после завершения drag."""
        had_line = self._drop_line_y is not None
        self._drop_line_y = None
        if had_line:
            self.viewport().update()
        if self._drop_hint:
            self._drop_hint = ""
            self.drop_hint_changed.emit("")

    def _set_drop_feedback(self, plan, hint):
        line_y = None
        if plan is not None:
            _sources, _parent, _group, target, before = plan
            rect = self.visualItemRect(target)
            if rect.isValid():
                line_y = rect.top() if before else rect.bottom() + 1
                line_y = max(1, min(line_y, self.viewport().height() - 2))
        if line_y != self._drop_line_y:
            self._drop_line_y = line_y
            self.viewport().update()
        if hint != self._drop_hint:
            self._drop_hint = hint
            self.drop_hint_changed.emit(hint)

    def _validated_sources(self):
        sources = self.selectedItems()
        if not sources:
            return None, "Нет выбранных элементов"
        parents = {item.parent() for item in sources}
        groups = {self.item_order_group(item) for item in sources}
        favorite_buckets = {self._favorite_bucket(item) for item in sources}
        if len(parents) != 1 or len(groups) != 1 or len(favorite_buckets) != 1:
            return None, "Выберите элементы одной группы для перетаскивания"
        group = next(iter(groups))
        if group is None:
            return None, "Этот элемент нельзя переупорядочить"
        return (sources, next(iter(parents)), group,
                next(iter(favorite_buckets))), ""

    def _drop_plan(self, point):
        """Рассчитать вставку по половине полной строки под курсором."""
        source_data, reason = self._validated_sources()
        if source_data is None:
            return None, reason
        sources, source_parent, group, favorite_bucket = source_data

        target = self.itemAt(point)
        if target is None:
            return None, "Наведите указатель на строку целевой группы"
        if target in sources:
            return None, "Выберите соседнюю строку как место вставки"
        if target.parent() is not source_parent or self.item_order_group(target) != group:
            return None, "Порядок можно менять только внутри одной группы"
        if self._favorite_bucket(target) != favorite_bucket:
            return None, "Избранные и обычные элементы упорядочиваются отдельно"

        rect = self.visualItemRect(target)
        if not rect.isValid():
            return None, "Наведите указатель на видимую строку"
        before = point.y() < rect.top() + rect.height() / 2
        side = "перед" if before else "после"
        hint = f"Вставить {side} «{self._item_name(target)}»"
        return (sources, source_parent, group, target, before), hint

    @staticmethod
    def _siblings(parent, tree):
        if parent is None:
            return [tree.topLevelItem(i)
                    for i in range(tree.topLevelItemCount())]
        return [parent.child(i) for i in range(parent.childCount())]

    def _apply_reorder(self, plan):
        """Переставить элементы без стандартного Qt InternalMove.

        Базовая реализация Qt удаляет исходные строки после MoveAction. Здесь
        drop уже выполнен вручную, поэтому drag также запускается нашим
        ``startDrag`` и повторного удаления нет.
        """
        sources, parent, _group, target, before = plan
        siblings = self._siblings(parent, self)
        ordered_sources = [item for item in siblings if item in sources]
        remaining = [item for item in siblings if item not in sources]
        if not ordered_sources or target not in remaining:
            return False

        insert_at = remaining.index(target) + (0 if before else 1)
        new_order = (remaining[:insert_at] + ordered_sources
                     + remaining[insert_at:])
        if new_order == siblings:
            return False

        current = self.currentItem()
        was_blocked = self.blockSignals(True)
        try:
            for item in ordered_sources:
                if parent is None:
                    self.takeTopLevelItem(self.indexOfTopLevelItem(item))
                else:
                    parent.takeChild(parent.indexOfChild(item))
            for offset, item in enumerate(ordered_sources):
                if parent is None:
                    self.insertTopLevelItem(insert_at + offset, item)
                else:
                    parent.insertChild(insert_at + offset, item)
            for item in ordered_sources:
                item.setSelected(True)
            if current is not None:
                self.setCurrentItem(
                    current, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            self.blockSignals(was_blocked)
        self.viewport().update()
        return True

    def startDrag(self, _supported_actions):
        """Запустить Move drag без автоматического удаления строк Qt."""
        source_data, _reason = self._validated_sources()
        if source_data is None:
            self.drop_rejected.emit()
            return
        sources, _parent, _group, _favorite_bucket = source_data
        indexes = [self.indexFromItem(item, 0) for item in sources]
        mime_data = self.model().mimeData(indexes)
        if mime_data is None:
            return

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        preview_item = self.currentItem()
        if preview_item not in sources:
            preview_item = sources[0]
        rect = self.visualItemRect(preview_item).intersected(
            self.viewport().rect())
        if rect.isValid() and not rect.isEmpty():
            drag.setPixmap(self.viewport().grab(rect))
            hotspot = self.viewport().mapFromGlobal(QCursor.pos()) - rect.topLeft()
            drag.setHotSpot(hotspot)
        try:
            drag.exec(Qt.DropAction.MoveAction, Qt.DropAction.MoveAction)
        finally:
            self.clear_drop_feedback()

    def dragEnterEvent(self, event):
        if event.source() is self:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        # QAbstractItemView сохраняет штатную прокрутку у верхней/нижней границы.
        super().dragMoveEvent(event)
        if event.source() is not self:
            self.clear_drop_feedback()
            event.ignore()
            return
        plan, hint = self._drop_plan(event.position().toPoint())
        self._set_drop_feedback(plan, hint)
        if plan is None:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def dragLeaveEvent(self, event):
        self.clear_drop_feedback()
        super().dragLeaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_line_y is None:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self._drop_indicator_color, 3,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(3, self._drop_line_y,
                         max(3, self.viewport().width() - 4), self._drop_line_y)
        painter.end()

    def dropEvent(self, event):
        plan, _hint = self._drop_plan(event.position().toPoint())
        self.clear_drop_feedback()
        if plan is None:
            event.ignore()
            self.drop_rejected.emit()
            return
        _sources, parent, group, _target, _before = plan
        if not self._apply_reorder(plan):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.order_changed.emit(parent, group)


class TreeMixin:
    """Построение/перестроение дерева, сортировка, поиск, контекстное меню и
    операции над папками/сервисами/аккаунтами."""

    # Префиксы-«иконки» для элементов дерева. Контейнеры/аккаунт/сервер — здесь;
    # фин-часть берётся из реестра дескрипторов (единый источник tree_prefix,
    # без дублирования).
    PREFIX = {FOLDER: "[+] ", SERVICE: "[o] ", ACCOUNT: "(i) ", SERVER: "[#] ",
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
            elif node["type"] == SERVER:
                days = node.get("paid_days_left")
                if days is not None and days <= 0:
                    text += "  [!]"   # оплата сервера просрочена
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
        from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

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

    def on_search_changed(self, text):
        self.search_text = text.strip().lower()
        self._apply_filter()
        self._update_dnd_mode()
        self._update_tree_toggle_btn()

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

    def on_tree_order_changed(self, parent_item, group):
        """Сохраняет новый порядок после перетаскивания (async — запись не виснет
        на UI, H-8). Синхронно снимает id только изменённой логической группы;
        запись выполняется фоновым воркером БД."""
        allowed_groups = {
            None: {FOLDER, SERVICE, ACCOUNT, AccountTree.FIN_GROUP, SERVER},
            FOLDER: {SERVICE},
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
            elif t == SERVER:
                # Те же пункты, что у фин-листа, плюс переименование (правка
                # имени доступна и отсюда, независимо от карточки).
                self._add_move_server_to_service_menu(menu, selected)
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
                menu.addAction("В избранное", lambda: self._set_favorite(selected, True))
                menu.addAction("Убрать из избранного", lambda: self._set_favorite(selected, False))
                menu.addSeparator()
                menu.addAction("Удалить", lambda: self.delete_items(selected))
            elif types == {SERVER}:
                self._add_move_server_to_service_menu(menu, selected)
                menu.addAction("В избранное", lambda: self._set_server_favorite(selected, True))
                menu.addAction("Убрать из избранного",
                               lambda: self._set_server_favorite(selected, False))
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

    def _add_move_server_to_service_menu(self, menu, selected):
        """Перемещение серверов между сервисами (по образцу
        _add_move_fin_to_service_menu — без создания нового сервиса)."""
        sub = menu.addMenu("Переместить в сервис")
        for s in self.db.get_services():
            sub.addAction(s["name"],
                          lambda checked=False, sid=s["id"]: self._move_servers(selected, sid))
        sub.addSeparator()
        sub.addAction("Сделать свободным (без сервиса)",
                      lambda: self._move_servers(selected, None))

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

    def _server_ids(self, selected):
        """id выбранных серверов."""
        return [n["id"] for n in (self._node(i) for i in selected)
                if n["type"] == SERVER]

    def _set_server_favorite(self, selected, value):
        ids = self._server_ids(selected)
        # Пакетный метод — ОДНА транзакция БД (M7-04), по образцу
        # _set_fin_favorite/set_fin_favorites.
        util.fire(self._run_then_reload(
            self.db.set_server_favorites, (ids, value), "Не удалось выполнить операцию"))

    def _move_servers(self, selected, service_id):
        ids = self._server_ids(selected)
        util.fire(self._run_then_reload(
            self.db.move_servers, (ids, service_id),
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
        name = name.strip()
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
        name = name.strip()
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

    def add_server(self):
        """Создание VPS-сервера (docs/ТЗ_VPS_Серверы.md §4). Флоу как у
        аккаунта/фин-записи: контейнер (выбранный сервис или корень) → имя →
        пустая запись в БД → выбор узла → карточка сразу в правке."""
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
            # Папка или ничего не выбрано → свободный сервер (в корне).
            service_id = None

        name, ok = theme.themed_input(
            self.config, self, "Новый сервер", "Название:")
        name = name.strip()
        if ok and name:
            util.fire(self._add_server_async(service_id, name))

    async def _add_server_async(self, service_id, name):
        session = self.db.current_session()
        try:
            server_id = await self.db.run_async(
                self.db.add_server, service_id, name, _session=session)
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
