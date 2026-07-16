"""Виджет привязанных серверов на карточке аккаунта (docs/ТЗ_VPS_Серверы.md §4).

Клон LinkedFinItemsWidget (fin_linked.py) под серверы: строка вида
«[#] Имя сервера» (+ «[!]», если оплата просрочена), клик — навигация к
серверу в дереве. Данные приходят из db.get_account_server_links (только
живые серверы — записи в корзине не показываются, связь в БД сохраняется).
Независим от fin_linked.py — сервер не финансовый лист (docs §2), поэтому
навигация без node_type (он всегда SERVER)."""
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
                               QLabel)
from PySide6.QtCore import Signal

from hranilka.core.domain import days_until_paid_until

SERVER_TREE_PREFIX = "[#] "


def server_item_display(link: dict) -> str:
    """Подпись сервера: префикс + имя + «[!]», если оплата просрочена/скоро
    истекает (тот же порог, что и маркер дерева — ui/tree.py)."""
    text = SERVER_TREE_PREFIX + (link.get("name") or "")
    days = days_until_paid_until(link.get("paid_until"))
    if days is not None and days <= 0:
        text += "  [!]"
    return text


class LinkedServersWidget(QWidget):
    """Список привязанных серверов. Клик по строке — навигация в дереве."""

    navigate_requested = Signal(int)   # server_id
    add_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(5)

        self.add_btn = QPushButton("+ ПРИВЯЗАТЬ")
        self.add_btn.clicked.connect(self.add_requested.emit)
        self.layout.addWidget(self.add_btn)

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(3)
        self.layout.addLayout(self.rows_layout)

        self.empty_label = QLabel("(нет привязанных серверов)")
        self.layout.addWidget(self.empty_label)
        self.layout.addStretch()

        # (server_id, row_widget, del_btn)
        self.items: list[tuple[int, QWidget, QPushButton]] = []
        self._editable = False

    def _add_row(self, link: dict) -> None:
        server_id = link["id"]
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)

        link_btn = QPushButton(server_item_display(link))
        link_btn.setToolTip("Перейти к серверу в дереве")
        link_btn.clicked.connect(
            lambda: self.navigate_requested.emit(server_id))
        h.addWidget(link_btn, 1)

        del_btn = QPushButton("[X]", row)       # родитель сразу — см. linked.py
        del_btn.setFixedWidth(40)
        del_btn.setVisible(self._editable)
        del_btn.clicked.connect(lambda: self._remove_row(row))
        h.addWidget(del_btn)

        self.rows_layout.addWidget(row)
        self.items.append((server_id, row, del_btn))
        self._update_empty()

    def _remove_row(self, row: QWidget) -> None:
        for i, (_sid, w, _btn) in enumerate(self.items):
            if w is row:
                self.items.pop(i)
                break
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self._update_empty()

    def _update_empty(self) -> None:
        self.empty_label.setVisible(not self.items)

    def set_data(self, links: list[dict]) -> None:
        """links — строки get_account_server_links: id, name, paid_until."""
        for _sid, w, _btn in self.items:
            w.hide()
            self.rows_layout.removeWidget(w)
            w.deleteLater()
        self.items.clear()
        for link in links:
            self._add_row(link)
        self._update_empty()

    def get_data(self) -> list[int]:
        """id привязанных серверов (для сохранения связей)."""
        return [sid for sid, _w, _btn in self.items]

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self.add_btn.setVisible(editable)
        for _sid, _row, del_btn in self.items:
            del_btn.setVisible(editable)
