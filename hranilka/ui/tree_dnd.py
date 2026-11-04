"""AccountTree: дерево с ограниченным drag&drop (выделено из tree.py)."""
from PySide6.QtWidgets import QTreeWidget
from PySide6.QtCore import Qt, Signal, QItemSelectionModel
from PySide6.QtGui import QColor, QCursor, QDrag, QPainter, QPen

from hranilka.core.nodetypes import FIN_LEAF_TYPES, FOLDER, LEAF_TYPES


class AccountTree(QTreeWidget):
    """Дерево с безопасным переупорядочиванием внутри логической группы.

    Вся верхняя половина строки означает вставку перед ней, нижняя — после.
    Перенос между родителями остаётся в контекстном меню. Карты и кошельки
    считаются одной группой, потому что хранят общий ``sort_order``.
    """

    FIN_GROUP = "fin_items"

    # Параметры: элемент-родитель (или None для корня) и логическая группа.
    order_changed = Signal(object, str)
    # Drop листьев на папку: (список перетаскиваемых элементов, элемент-папка).
    reparent_requested = Signal(object, object)
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

    def _reparent_plan(self, point):
        """Drop листьев на строку ПАПКИ - перенос внутрь, минуя сервис.

        Возвращает ((sources, folder_item), hint) либо (None, reason). Источники
        - любые выделенные листья (аккаунт/карта/кошелёк/сервер, в т.ч. разных
        типов и родителей). None-план без причины ("") - цель не папка и
        вызывающий пробует обычное переупорядочивание."""
        target = self.itemAt(point)
        if target is None or self._item_type(target) != FOLDER:
            return None, ""
        sources = self.selectedItems()
        if not sources or any(self._item_type(i) not in LEAF_TYPES
                              for i in sources):
            return None, ""
        if all(i.parent() is target for i in sources):
            return None, "Элементы уже лежат в этой папке"
        hint = f"Переместить в папку «{self._item_name(target)}»"
        return (sources, target), hint

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
        if source_data is not None:
            sources = source_data[0]
        else:
            # Смесь листьев разных групп/родителей: порядок менять нельзя, но
            # перенос в папку (reparent) допустим.
            sources = self.selectedItems()
            if not sources or any(self._item_type(i) not in LEAF_TYPES
                                  for i in sources):
                self.drop_rejected.emit()
                return
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
        point = event.position().toPoint()
        reparent, reparent_hint = self._reparent_plan(point)
        if reparent is not None:
            self._set_drop_feedback(None, reparent_hint)
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        plan, hint = self._drop_plan(point)
        self._set_drop_feedback(plan, reparent_hint or hint)
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
        point = event.position().toPoint()
        reparent, _rhint = self._reparent_plan(point)
        if reparent is not None:
            self.clear_drop_feedback()
            sources, folder_item = reparent
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            self.reparent_requested.emit(list(sources), folder_item)
            return
        plan, _hint = self._drop_plan(point)
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
