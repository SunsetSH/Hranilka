"""Корзина удалённых аккаунтов (вынесена из dialogs.py, этап 4)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton)

from hranilka.ui.theme import ThemedDialog, themed_confirm


class RecycleBinDialog(ThemedDialog):
    """Корзина: список удалённых аккаунтов с восстановлением и безвозвратным
    удалением. self.changed = True, если что-то восстановили/удалили (тогда
    главное окно перестроит дерево и обновит кнопку корзины)."""

    def __init__(self, config, db, parent=None):
        super().__init__(config, parent)
        self._db = db
        self.changed = False
        self.setWindowTitle("Корзина")
        self.setModal(True)
        self.setMinimumSize(460, 360)

        lay = self.body
        lay.addWidget(QLabel("Удалённые аккаунты:"))
        self._list = QListWidget()
        lay.addWidget(self._list, 1)

        self._empty_label = QLabel("Корзина пуста.")
        self._empty_label.setWordWrap(True)
        lay.addWidget(self._empty_label)

        row = QHBoxLayout()
        self._restore_btn = QPushButton("Восстановить")
        self._restore_btn.clicked.connect(self._restore)
        self._del_btn = QPushButton("Удалить навсегда")
        self._del_btn.clicked.connect(self._delete_forever)
        self._empty_btn = QPushButton("Очистить корзину")
        self._empty_btn.clicked.connect(self._empty)
        row.addWidget(self._restore_btn)
        row.addWidget(self._del_btn)
        row.addWidget(self._empty_btn)
        lay.addLayout(row)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        lay.addLayout(close_row)

        self._refresh()

    def _refresh(self):
        self._list.clear()
        rows = self._db.get_deleted_accounts()
        for r in rows:
            stamp = str(r["deleted_at"] or "")[:19]
            label = r["name"] if not stamp else f"{r['name']}   (удалён: {stamp})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, r["id"])
            self._list.addItem(item)
        has_items = bool(rows)
        self._empty_label.setVisible(not has_items)
        self._list.setVisible(has_items)
        for b in (self._restore_btn, self._del_btn, self._empty_btn):
            b.setEnabled(has_items)
        if has_items:
            self._list.setCurrentRow(0)

    def _current_id(self):
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _restore(self):
        aid = self._current_id()
        if aid is None:
            return
        self._db.restore_account(aid)
        self.changed = True
        self._refresh()

    def _delete_forever(self):
        aid = self._current_id()
        if aid is None:
            return
        if not themed_confirm(self.config, self, "Удаление",
                              "Удалить аккаунт безвозвратно?\nЭто действие необратимо."):
            return
        self._db.delete_account(aid)
        self.changed = True
        self._refresh()

    def _empty(self):
        if self._db.get_deleted_count() == 0:
            return
        if not themed_confirm(self.config, self, "Очистка корзины",
                              "Безвозвратно удалить ВСЕ аккаунты из корзины?\n"
                              "Это действие необратимо."):
            return
        self._db.empty_bin()
        self.changed = True
        self._refresh()

