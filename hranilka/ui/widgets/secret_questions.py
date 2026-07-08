"""Виджет секретных вопросов (вынесен из widgets.py, этап 5)."""
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton, QLabel)
from hranilka.ui.widgets.common import _warn, _confirm


class SecretQuestionsWidget(QWidget):
    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(10)
        self.rows = []
        self._editable = False

        self.add_btn = QPushButton("+ ДОБАВИТЬ ВОПРОС")
        self.add_btn.clicked.connect(lambda: self.add_row())
        self.layout.addWidget(self.add_btn)
        self.empty_label = QLabel("Секретных вопросов нет")
        self.layout.addWidget(self.empty_label)
        self.layout.addStretch()
        self._update_empty()

    def _update_empty(self):
        self.empty_label.setVisible(not self.rows)

    def add_row(self, q="", a=""):
        if len(self.rows) >= 5:
            _warn(self.config, self, "Лимит", "Максимум 5 вопросов!")
            return
            
        row_widget = QWidget(self)
        h_layout = QHBoxLayout(row_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(5)

        q_edit = QLineEdit(str(q))
        q_edit.setPlaceholderText("Секретный вопрос...")
        q_edit.setReadOnly(not self._editable)
        a_edit = QLineEdit(str(a))
        a_edit.setPlaceholderText("Ответ...")
        a_edit.setReadOnly(not self._editable)

        del_btn = QPushButton("[X]", row_widget)   # родитель сразу — см. add_item
        del_btn.setFixedWidth(40)
        del_btn.setVisible(self._editable)
        del_btn.clicked.connect(lambda: self.remove_row(row_widget))
        
        h_layout.addWidget(q_edit)
        h_layout.addWidget(a_edit)
        h_layout.addWidget(del_btn)

        self.layout.insertWidget(self.layout.count() - 2, row_widget)
        # Храним прямые ссылки на виджеты строки (L-9): без хрупких поисков
        # через parent()/layout().itemAt(...).
        self.rows.append((q_edit, a_edit, del_btn, row_widget))
        self._update_empty()

    def remove_row(self, row_widget):
        if not _confirm(self.config, self, "Удаление",
                        "Удалить этот секретный вопрос?"):
            return
        for i, (_q, _a, _btn, w) in enumerate(self.rows):
            if w == row_widget:
                self.rows.pop(i)
                break
        self.layout.removeWidget(row_widget)
        row_widget.deleteLater()
        self._update_empty()

    def get_data(self):
        # Не сохраняем строку, если оба связанных поля пустые.
        return [{"q": q.text(), "a": a.text()} for q, a, _btn, _w in self.rows
                if q.text().strip() or a.text().strip()]

    def set_data(self, data):
        for _q, _a, _btn, row_widget in self.rows:
            self.layout.removeWidget(row_widget)
            row_widget.deleteLater()
        self.rows.clear()
        for item in data: self.add_row(item.get("q", ""), item.get("a", ""))
        self._update_empty()
    def set_editable(self, editable):
        self._editable = editable
        self.add_btn.setVisible(editable)
        for q, a, del_btn, _w in self.rows:
            q.setReadOnly(not editable)
            a.setReadOnly(not editable)
            del_btn.setVisible(editable)
