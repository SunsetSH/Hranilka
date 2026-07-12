"""Виджет привязанных финансовых записей на карточке аккаунта (концепт §8).

Клон LinkedAccountsWidget (linked.py) под fin_items: строка вида
«[$] Tinkoff Black •1234» / «[₿] Холодный кошелёк», клик — навигация к записи
в дереве. Данные приходят из db.get_account_fin_links (только живые записи —
элементы в корзине не показываются, связь с ними в БД сохраняется).
"""
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
                               QLabel)
from PySide6.QtCore import Signal

from hranilka.core.fin_types import FIN_TYPES
from hranilka.core.nodetypes import CARD


def fin_item_display(link: dict) -> str:
    """Подпись записи: префикс типа + имя + «•1234» для карт. Используется и
    строками виджета, и диалогом выбора «+ ПРИВЯЗАТЬ»."""
    spec = FIN_TYPES.get(link.get("item_type") or "")
    prefix = spec.tree_prefix if spec else ""
    text = prefix + (link.get("name") or "")
    if link.get("card_last4"):
        text += f" •{link['card_last4']}"
    return text


def _node_type_of(link: dict) -> str:
    """Тип узла дерева для навигации (неизвестный тип — как карта)."""
    spec = FIN_TYPES.get(link.get("item_type") or "")
    return spec.node_type if spec else CARD


class LinkedFinItemsWidget(QWidget):
    """Список привязанных карт/кошельков. Клик по строке — навигация в дереве."""

    navigate_requested = Signal(str, int)   # (node_type, item_id)
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

        self.empty_label = QLabel("(нет привязанных карт и кошельков)")
        self.layout.addWidget(self.empty_label)
        self.layout.addStretch()

        # (item_id, node_type, row_widget, del_btn)
        self.items: list[tuple[int, str, QWidget, QPushButton]] = []
        self._editable = False

    def _add_row(self, link: dict) -> None:
        item_id = link["id"]
        node_type = _node_type_of(link)
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)

        link_btn = QPushButton(fin_item_display(link))
        link_btn.setToolTip("Перейти к записи в дереве")
        link_btn.clicked.connect(
            lambda: self.navigate_requested.emit(node_type, item_id))
        h.addWidget(link_btn, 1)

        del_btn = QPushButton("[X]", row)       # родитель сразу — см. linked.py
        del_btn.setFixedWidth(40)
        del_btn.setVisible(self._editable)
        del_btn.clicked.connect(lambda: self._remove_row(row))
        h.addWidget(del_btn)

        self.rows_layout.addWidget(row)
        self.items.append((item_id, node_type, row, del_btn))
        self._update_empty()

    def _remove_row(self, row: QWidget) -> None:
        for i, (_iid, _nt, w, _btn) in enumerate(self.items):
            if w is row:
                self.items.pop(i)
                break
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self._update_empty()

    def _update_empty(self) -> None:
        self.empty_label.setVisible(not self.items)

    def set_data(self, links: list[dict]) -> None:
        """links — строки get_account_fin_links: id, item_type, name, card_last4."""
        for _iid, _nt, w, _btn in self.items:
            w.hide()
            self.rows_layout.removeWidget(w)
            w.deleteLater()
        self.items.clear()
        for link in links:
            self._add_row(link)
        self._update_empty()

    def get_data(self) -> list[int]:
        """id привязанных записей (для сохранения связей)."""
        return [iid for iid, _nt, _w, _btn in self.items]

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self.add_btn.setVisible(editable)
        for _iid, _nt, _row, del_btn in self.items:
            del_btn.setVisible(editable)
