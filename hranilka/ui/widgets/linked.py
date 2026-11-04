"""Виджеты связей карточки аккаунта: связанные аккаунты, привязанные
финансовые записи и серверы (аудит 2026-11, раздел 8).

Общий каркас - LinkedItemsWidget (кнопка «добавить», строки «ссылка + [X]»,
метка «пусто», editable-режим); подклассы задают тексты, подпись строки,
форму записи в self.items и сигнал навигации. Старые модули fin_linked.py /
srv_linked.py реэкспортируют имена отсюда."""
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
                               QLabel)
from PySide6.QtCore import Signal

from hranilka.core.domain import days_until_paid_until
from hranilka.core.fin_types import FIN_TYPES
from hranilka.core.nodetypes import CARD
from hranilka.ui.widgets.common import make_delete_button

SERVER_TREE_PREFIX = "[#] "


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
    """Тип узла дерева для навигации (неизвестный тип - как карта)."""
    spec = FIN_TYPES.get(link.get("item_type") or "")
    return spec.node_type if spec else CARD


def server_item_display(link: dict) -> str:
    """Подпись сервера: префикс + имя + «[!]», если оплата просрочена/скоро
    истекает (тот же порог, что и маркер дерева - ui/tree.py)."""
    text = SERVER_TREE_PREFIX + (link.get("name") or "")
    days = days_until_paid_until(link.get("paid_until"))
    if days is not None and days <= 0:
        text += "  [!]"
    return text


class LinkedItemsWidget(QWidget):
    """Базовый список ссылок. Запись self.items - кортеж, заканчивающийся
    (row_widget, del_btn); первый элемент - id. Подклассы определяют форму
    через _make_entry и испускают свой navigate_requested в _emit_navigate."""

    add_requested = Signal()

    ADD_TEXT = "+ ПРИВЯЗАТЬ"
    EMPTY_TEXT = "(пусто)"
    NAV_TOOLTIP = "Перейти"
    DEL_NAME = "Убрать связь"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(5)

        self.add_btn = QPushButton(self.ADD_TEXT)
        self.add_btn.clicked.connect(self.add_requested.emit)
        self.layout.addWidget(self.add_btn)

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(3)
        self.layout.addLayout(self.rows_layout)

        self.empty_label = QLabel(self.EMPTY_TEXT)
        self.layout.addWidget(self.empty_label)
        self.layout.addStretch()

        self.items: list[tuple] = []
        self._editable = False

    # ----- хуки подклассов -----

    def _row_label(self, link: dict) -> str:
        raise NotImplementedError

    def _make_entry(self, link: dict, row: QWidget, del_btn: QPushButton) -> tuple:
        raise NotImplementedError

    def _emit_navigate(self, link: dict) -> None:
        raise NotImplementedError

    # ----- общая логика -----

    def _add_row(self, link: dict) -> None:
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)

        link_btn = QPushButton(self._row_label(link))
        link_btn.setToolTip(self.NAV_TOOLTIP)
        link_btn.clicked.connect(
            lambda checked=False, lk=link: self._emit_navigate(lk))
        h.addWidget(link_btn, 1)

        del_btn = make_delete_button(row, self.DEL_NAME)  # родитель сразу
        del_btn.setVisible(self._editable)
        del_btn.clicked.connect(lambda: self._remove_row(row))
        h.addWidget(del_btn)

        self.rows_layout.addWidget(row)
        self.items.append(self._make_entry(link, row, del_btn))
        self._update_empty()

    def _remove_row(self, row: QWidget) -> None:
        for i, entry in enumerate(self.items):
            if entry[-2] is row:
                self.items.pop(i)
                break
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self._update_empty()

    def _update_empty(self) -> None:
        self.empty_label.setVisible(not self.items)

    def set_data(self, links: list[dict]) -> None:
        self.setUpdatesEnabled(False)
        try:
            for entry in self.items:
                w = entry[-2]
                w.hide()
                self.rows_layout.removeWidget(w)
                w.deleteLater()
            self.items.clear()
            for link in links:
                self._add_row(link)
            self._update_empty()
        finally:
            self.setUpdatesEnabled(True)

    def get_data(self) -> list[int]:
        return [entry[0] for entry in self.items]

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self.add_btn.setVisible(editable)
        for entry in self.items:
            entry[-1].setVisible(editable)


class LinkedAccountsWidget(LinkedItemsWidget):
    """Список связанных аккаунтов. Клик - навигация к аккаунту в дереве.
    items: (account_id, name, row_widget, del_btn)."""

    navigate_requested = Signal(int)   # account_id

    ADD_TEXT = "+ СВЯЗАТЬ АККАУНТ"
    EMPTY_TEXT = "(нет связанных аккаунтов)"
    NAV_TOOLTIP = "Перейти к связанному аккаунту"
    DEL_NAME = "Убрать связанный аккаунт"

    def _row_label(self, link):
        return link["name"]

    def _make_entry(self, link, row, del_btn):
        return (link["id"], link["name"], row, del_btn)

    def _emit_navigate(self, link):
        self.navigate_requested.emit(link["id"])


class LinkedFinItemsWidget(LinkedItemsWidget):
    """Список привязанных карт/кошельков. items: (item_id, node_type, row, del)."""

    navigate_requested = Signal(str, int)   # (node_type, item_id)

    EMPTY_TEXT = "(нет привязанных карт и кошельков)"
    NAV_TOOLTIP = "Перейти к записи в дереве"
    DEL_NAME = "Отвязать запись"

    def _row_label(self, link):
        return fin_item_display(link)

    def _make_entry(self, link, row, del_btn):
        return (link["id"], _node_type_of(link), row, del_btn)

    def _emit_navigate(self, link):
        self.navigate_requested.emit(_node_type_of(link), link["id"])


class LinkedServersWidget(LinkedItemsWidget):
    """Список привязанных серверов. items: (server_id, row, del)."""

    navigate_requested = Signal(int)   # server_id

    EMPTY_TEXT = "(нет привязанных серверов)"
    NAV_TOOLTIP = "Перейти к серверу в дереве"
    DEL_NAME = "Отвязать сервер"

    def _row_label(self, link):
        return server_item_display(link)

    def _make_entry(self, link, row, del_btn):
        return (link["id"], row, del_btn)

    def _emit_navigate(self, link):
        self.navigate_requested.emit(link["id"])
