"""AccountTree: дерево с drag&drop (выделено из tree.py).

Жест начинается здесь, а не в QAbstractItemView: базовый класс решает «тащить
или выделять» по своему pressedIndex, который теряется при перестроении дерева,
и при выключенном drag превращает протягивание в выделение диапазона."""
from dataclasses import dataclass
from typing import Optional

from PySide6.QtWidgets import QAbstractItemView, QApplication, QTreeWidget, QTreeWidgetItem
from PySide6.QtCore import Qt, Signal, QItemSelectionModel
from PySide6.QtGui import QColor, QCursor, QDrag, QPainter, QPen

from hranilka.core.nodetypes import (FIN_LEAF_TYPES, FOLDER, LEAF_TYPES,
                                     SERVICE)

_LEAF_KIND = "leaf"
_ZONE_BEFORE, _ZONE_INTO, _ZONE_AFTER, _ZONE_END = range(4)
_CONTAINER_EDGE = 0.25        # доля строки контейнера под вставку рядом с ним


@dataclass(frozen=True)
class MovePlan:
    """Перенос в другого родителя. parent=None — корень; anchor — строка, рядом
    с которой вставить (None — в конец), before — перед ней или после."""
    sources: tuple
    parent: Optional[QTreeWidgetItem]
    anchor: Optional[QTreeWidgetItem]
    before: bool


class AccountTree(QTreeWidget):
    """Дерево с переупорядочиванием внутри группы и переносом между родителями.

    На листе верхняя половина строки — вставка перед ней, нижняя — после. На
    папке/сервисе края строки — вставка рядом, середина — внутрь. Карты и
    кошельки считаются одной группой: они хранят общий ``sort_order``.
    """

    FIN_GROUP = "fin_items"

    # Параметры: элемент-родитель (или None для корня) и логическая группа.
    order_changed = Signal(object, str)
    move_requested = Signal(object)      # MovePlan
    drop_rejected = Signal(str)          # причина для строки статуса
    drop_hint_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drop_line = None           # (x, y) начала линии вставки
        self._drop_into_item = None      # подсвеченный контейнер
        self._drop_hint = ""
        self._drop_indicator_color = self.palette().text().color()
        self._reorder_enabled = True
        self._press_pos = None           # точка простого нажатия ЛКМ на строке
        self._press_consumed = False     # жест уже обработан, Qt его не видит
        self.setAutoExpandDelay(600)     # раскрыть свёрнутую папку под курсором

    def set_reorder_enabled(self, enabled):
        """Порядок меняется только в ручной сортировке; перенос доступен всегда."""
        self._reorder_enabled = bool(enabled)

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
        if self._drop_line is not None or self._drop_into_item is not None:
            self.viewport().update()

    def clear_drop_feedback(self):
        """Убрать индикатор вставки и подсказку после завершения drag."""
        had_mark = self._drop_line is not None or self._drop_into_item is not None
        self._drop_line = None
        self._drop_into_item = None
        if had_mark:
            self.viewport().update()
        if self._drop_hint:
            self._drop_hint = ""
            self.drop_hint_changed.emit("")

    def _set_drop_feedback(self, plan, hint):
        line, into = self._feedback_marks(plan)
        if line != self._drop_line or into is not self._drop_into_item:
            self._drop_line = line
            self._drop_into_item = into
            self.viewport().update()
        if hint != self._drop_hint:
            self._drop_hint = hint
            self.drop_hint_changed.emit(hint)

    def _feedback_marks(self, plan):
        """Что рисовать для плана: линию вставки и/или рамку контейнера."""
        if plan is None:
            return None, None
        if isinstance(plan, MovePlan):
            if plan.anchor is None:
                return None, plan.parent
            return self._line_at(plan.anchor, plan.before), None
        _sources, _parent, _group, target, before = plan
        return self._line_at(target, before), None

    def _line_at(self, item, before):
        rect = self.visualItemRect(item)
        if not rect.isValid():
            return None
        y = rect.top() if before else rect.bottom() + 1
        return rect.left(), max(1, min(y, self.viewport().height() - 2))

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

    def _source_kind(self, sources):
        """Вид перетаскиваемого набора: листья, сервисы, папки или None (смесь)."""
        types = {self._item_type(item) for item in sources}
        if types and types <= LEAF_TYPES:
            return _LEAF_KIND
        return next(iter(types)) if len(types) == 1 else None

    def _row_zone(self, point):
        """Строка под курсором и зона внутри неё."""
        target = self.itemAt(point)
        if target is None:
            return None, _ZONE_END
        rect = self.visualItemRect(target)
        offset = (point.y() - rect.top()) / max(1, rect.height())
        if self._item_type(target) not in (FOLDER, SERVICE):
            return target, _ZONE_BEFORE if offset < 0.5 else _ZONE_AFTER
        if offset < _CONTAINER_EDGE:
            return target, _ZONE_BEFORE
        # Под раскрытым контейнером идут его дети: «после него» там не вставить.
        expanded = target.isExpanded() and target.childCount() > 0
        if offset > 1 - _CONTAINER_EDGE and not expanded:
            return target, _ZONE_AFTER
        return target, _ZONE_INTO

    def _can_host(self, kind, parent):
        """Может ли родитель принять элементы этого вида (папки — только корень)."""
        if kind == FOLDER:
            return False
        if parent is None:
            return True
        return kind == _LEAF_KIND or self._item_type(parent) == FOLDER

    def _parent_title(self, parent):
        return (f"«{self._item_name(parent)}»" if parent is not None
                else "корень дерева")

    def _destination(self, point):
        """Куда указывает курсор: (родитель, строка-якорь, перед ней?, зона)."""
        target, zone = self._row_zone(point)
        if zone == _ZONE_END:
            return None, None, False, zone
        if zone == _ZONE_INTO:
            return target, None, False, zone
        return target.parent(), target, zone == _ZONE_BEFORE, zone

    def _resolve_drop(self, point):
        """План drop под курсором: reorder-кортеж, MovePlan или (None, причина)."""
        sources = self.selectedItems()
        if not sources:
            return None, "Нет выбранных элементов"
        kind = self._source_kind(sources)
        if kind is None:
            return None, "Папки, сервисы и записи перетаскиваются раздельно"
        parent, anchor, before, zone = self._destination(point)
        if anchor in sources or parent in sources:
            return None, "Выберите другое место для вставки"
        if all(item.parent() is parent for item in sources):
            return self._same_parent_drop(point, parent, zone)
        if not self._can_host(kind, parent):
            return None, ("Папки лежат только в корне дерева" if kind == FOLDER
                          else "Сервис нельзя вложить в другой сервис")
        if not self._reorder_enabled:
            anchor = None                # место определит текущая сортировка
        plan = MovePlan(tuple(sources), parent, anchor, before)
        return plan, f"Переместить в {self._parent_title(parent)}"

    def _same_parent_drop(self, point, parent, zone):
        if zone == _ZONE_INTO:
            return None, f"Элементы уже лежат в {self._parent_title(parent)}"
        if not self._reorder_enabled:
            return None, "Порядок меняется только в ручной сортировке"
        if zone == _ZONE_END:
            return None, "Наведите указатель на строку целевой группы"
        return self._drop_plan(point)

    @staticmethod
    def siblings_of(tree, parent):
        """Все дети родителя (None — корень), включая скрытые фильтром."""
        return AccountTree._siblings(parent, tree)

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

    def mousePressEvent(self, event):
        # Сбой доставки отпускания (меню, модальное окно) оставлял бы view в
        # DragSelecting: тогда следующее протягивание снова выделяло бы строки.
        self.setState(QAbstractItemView.State.NoState)
        self._press_consumed = False
        self._press_pos = self._plain_press_point(event)
        super().mousePressEvent(event)

    def _plain_press_point(self, event):
        """Точка нажатия, с которой начинается перетаскивание, иначе None."""
        modifiers = Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
        if (event.button() != Qt.MouseButton.LeftButton
                or event.modifiers() & modifiers):
            return None
        point = event.position().toPoint()
        item = self.itemAt(point)
        # Левее текста — стрелка ветки и отступ: это не захват строки.
        if item is None or point.x() < self.visualItemRect(item).left():
            return None
        return point

    def mouseMoveEvent(self, event):
        buttons = event.buttons()
        if buttons & Qt.MouseButton.RightButton:
            return                       # ПКМ — только контекстное меню
        if not buttons & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)
            return
        if self._press_consumed:
            return
        if self._press_pos is None:
            super().mouseMoveEvent(event)
            return
        distance = (event.position().toPoint() - self._press_pos).manhattanLength()
        if distance >= QApplication.startDragDistance():
            self._press_consumed = True
            self._begin_drag(self._press_pos)

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        self._press_consumed = False
        super().mouseReleaseEvent(event)

    def _begin_drag(self, point):
        """Строка ищется заново по точке: дерево могло перестроиться после
        нажатия, а прежний QTreeWidgetItem к этому моменту уже уничтожен."""
        item = self.itemAt(point)
        if item is None:
            return
        if not item.isSelected():
            self.setCurrentItem(item)
        if not self.dragEnabled():
            self.drop_rejected.emit(self.toolTip())
            return
        self.startDrag(Qt.DropAction.MoveAction)

    def startDrag(self, _supported_actions):
        """Запустить Move drag без автоматического удаления строк Qt."""
        sources = self.selectedItems()
        if not sources or self._source_kind(sources) is None:
            self.drop_rejected.emit(
                "Папки, сервисы и записи перетаскиваются раздельно")
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
            self.stopAutoScroll()
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
        plan, hint = self._resolve_drop(event.position().toPoint())
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
        if self._drop_line is None and self._drop_into_item is None:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self._drop_indicator_color, 3,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self._drop_line is not None:
            x, y = self._drop_line
            painter.drawLine(x, y, max(x, self.viewport().width() - 4), y)
        elif self._drop_into_item is not None:
            rect = self.visualItemRect(self._drop_into_item)
            if rect.isValid():
                rect.setRight(self.viewport().width() - 4)
                painter.drawRect(rect.adjusted(1, 1, -1, -1))
        painter.end()

    def dropEvent(self, event):
        plan, hint = self._resolve_drop(event.position().toPoint())
        self.clear_drop_feedback()
        if plan is None:
            event.ignore()
            self.drop_rejected.emit(hint)
            return
        if isinstance(plan, MovePlan):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            self.move_requested.emit(plan)
            return
        _sources, parent, group, _target, _before = plan
        if not self._apply_reorder(plan):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.order_changed.emit(parent, group)
