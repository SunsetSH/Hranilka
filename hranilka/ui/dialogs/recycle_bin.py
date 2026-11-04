"""Корзина удалённых записей: аккаунты и финансовые записи (вынесена из
dialogs.py, этап 4; обобщена до листовых типов, Фаза 1 фин-сущностей)."""
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton)

from hranilka.core.fin_types import FIN_TYPES
from hranilka.core.nodetypes import ACCOUNT, SERVER
from hranilka.ui.theme import ThemedDialog, themed_confirm, themed_info

# Ретро-префиксы типов записей в списке корзины: аккаунт + сервер + все
# финансовые типы из реестра (node_type → tree_prefix). Новый фин-тип получает
# префикс автоматически; сервер — своим литералом (не в реестре FIN_TYPES).
_TYPE_PREFIX = {ACCOUNT: "(i) ", SERVER: "[#] "}
_TYPE_PREFIX.update({spec.node_type: spec.tree_prefix
                     for spec in FIN_TYPES.values()})


class RecycleBinDialog(ThemedDialog):
    """Корзина: список удалённых записей (аккаунты + карты/кошельки) с
    восстановлением и безвозвратным удалением. self.changed = True, если что-то
    восстановили/удалили (тогда главное окно перестроит дерево и обновит кнопку
    корзины)."""

    # Ретро-префиксы типов записей в списке корзины (из реестра, см. модуль выше).
    _PREFIX = _TYPE_PREFIX

    def __init__(self, config, db, parent=None):
        super().__init__(config, parent)
        self._db = db
        self.changed = False
        self.setWindowTitle("Корзина")
        self.setModal(True)
        self.setMinimumSize(460, 360)

        lay = self.body
        lay.addWidget(QLabel("Удалённые записи:"))
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
        try:
            rows = self._db.get_deleted_records()
        except Exception as e:                       # noqa: BLE001
            logging.error("Корзина: не удалось прочитать записи: %s", e, exc_info=e)
            themed_info(self.config, self, "Ошибка",
                        f"Не удалось прочитать корзину:\n{e}")
            rows = []
        for r in rows:
            prefix = self._PREFIX.get(r["type"], "")
            stamp = str(r["deleted_at"] or "")[:19]
            base = prefix + r["name"]
            label = base if not stamp else f"{base}   (удалён: {stamp})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, (r["type"], r["id"]))
            self._list.addItem(item)
        has_items = bool(rows)
        self._empty_label.setVisible(not has_items)
        self._list.setVisible(has_items)
        for b in (self._restore_btn, self._del_btn, self._empty_btn):
            b.setEnabled(has_items)
        if has_items:
            self._list.setCurrentRow(0)

    def _current_key(self):
        """(type, id) выбранной записи или None."""
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _run_db(self, title: str, fn, *args) -> bool:
        """Выполнить операцию БД: при ошибке — сообщение пользователю, changed
        не выставляется. Список обновляется в любом случае."""
        try:
            fn(*args)
        except Exception as e:                       # noqa: BLE001
            logging.error("Корзина: %s: %s", title, e, exc_info=e)
            themed_info(self.config, self, "Ошибка", f"{title}:\n{e}")
            self._refresh()
            return False
        self.changed = True
        self._refresh()
        return True

    def _restore(self):
        key = self._current_key()
        if key is None:
            return
        node_type, rid = key
        if node_type == ACCOUNT:
            fn = self._db.restore_account
        elif node_type == SERVER:
            fn = self._db.restore_server
        else:
            fn = self._db.restore_fin_item
        self._run_db("Не удалось восстановить запись", fn, rid)

    def _delete_forever(self):
        key = self._current_key()
        if key is None:
            return
        if not themed_confirm(self.config, self, "Удаление",
                              "Удалить запись безвозвратно?\nЭто действие необратимо."):
            return
        node_type, rid = key
        if node_type == ACCOUNT:
            fn = self._db.delete_account
        elif node_type == SERVER:
            fn = self._db.delete_server_forever
        else:
            # delete_fin_item_forever — по id, не по node_type: запись может
            # быть типа, уже убранного из реестра (m010/m011-подобная чистка),
            # и delete_items() ошибочно принял бы её за аккаунт (см. баг M7-фин).
            fn = self._db.delete_fin_item_forever
        self._run_db("Не удалось удалить запись", fn, rid)

    def _empty(self):
        try:
            if self._db.get_deleted_count() == 0:
                return
        except Exception as e:                       # noqa: BLE001
            logging.error("Корзина: счётчик: %s", e, exc_info=e)
            themed_info(self.config, self, "Ошибка", f"Не удалось прочитать корзину:\n{e}")
            return
        if not themed_confirm(self.config, self, "Очистка корзины",
                              "Безвозвратно удалить ВСЕ записи из корзины?\n"
                              "Это действие необратимо."):
            return
        self._run_db("Не удалось очистить корзину", self._db.empty_bin)

