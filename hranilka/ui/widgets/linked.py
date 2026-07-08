"""Виджет связанных аккаунтов (вынесен из widgets.py, этап 5)."""
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel)
from PySide6.QtCore import Signal


class LinkedAccountsWidget(QWidget):
    """Список связанных аккаунтов. Клик по аккаунту — навигация к нему в дереве."""

    navigate_requested = Signal(int)   # account_id
    add_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(5)

        self.add_btn = QPushButton("+ СВЯЗАТЬ АККАУНТ")
        self.add_btn.clicked.connect(self.add_requested.emit)
        self.layout.addWidget(self.add_btn)

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(3)
        self.layout.addLayout(self.rows_layout)

        self.empty_label = QLabel("(нет связанных аккаунтов)")
        self.layout.addWidget(self.empty_label)
        self.layout.addStretch()

        # (account_id, name, row_widget)
        self.items = []
        self._editable = False

    def _add_row(self, account_id, name):
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)

        link_btn = QPushButton(name)
        link_btn.setToolTip("Перейти к связанному аккаунту")
        link_btn.clicked.connect(lambda: self.navigate_requested.emit(account_id))
        h.addWidget(link_btn, 1)

        del_btn = QPushButton("[X]", row)          # родитель сразу — см. add_item
        del_btn.setFixedWidth(40)
        del_btn.setVisible(self._editable)
        del_btn.clicked.connect(lambda: self._remove_row(row))
        h.addWidget(del_btn)

        self.rows_layout.addWidget(row)
        # Прямая ссылка на кнопку удаления (L-9) — без layout().itemAt(...).
        self.items.append((account_id, name, row, del_btn))
        self._update_empty()

    def _remove_row(self, row):
        for i, (_aid, _name, w, _btn) in enumerate(self.items):
            if w == row:
                self.items.pop(i)
                break
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self._update_empty()

    def _update_empty(self):
        self.empty_label.setVisible(not self.items)

    def set_data(self, links):
        for _aid, _name, w, _btn in self.items:
            w.hide()
            self.rows_layout.removeWidget(w)
            w.deleteLater()
        self.items.clear()
        for link in links:
            self._add_row(link["id"], link["name"])
        self._update_empty()

    def get_data(self):
        return [aid for aid, _name, _w, _btn in self.items]

    def set_editable(self, editable):
        self._editable = editable
        self.add_btn.setVisible(editable)
        for _aid, _name, _row, del_btn in self.items:
            del_btn.setVisible(editable)