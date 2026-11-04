"""Виджет секретных вопросов (вынесен из widgets.py, этап 5).

Ответы — секрет: в режиме просмотра маскируются (echo Password), вопросы
остаются видимыми. Раскрытие — одной кнопкой на всю вкладку (M-02)."""
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton, QLabel)
from hranilka.ui.widgets.common import (_warn, _confirm, RevealableRowsMixin,
                                        make_delete_button, set_a11y,
                                        updates_suspended)


class SecretQuestionsWidget(RevealableRowsMixin, QWidget):
    REVEAL_SHOW = "ПОКАЗАТЬ ОТВЕТЫ"
    REVEAL_HIDE = "СКРЫТЬ ОТВЕТЫ"

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(10)
        self.rows = []
        self._editable = False
        self._revealed = False

        self.add_btn = QPushButton("+ ДОБАВИТЬ ВОПРОС")
        self.add_btn.clicked.connect(lambda: self.add_row())
        self.reveal_btn = QPushButton("ПОКАЗАТЬ ОТВЕТЫ")
        self.reveal_btn.setCheckable(True)
        self.reveal_btn.setToolTip("Показать / скрыть все ответы")
        set_a11y(self.reveal_btn, "Показать или скрыть ответы")
        self.reveal_btn.toggled.connect(self._on_reveal_toggled)
        top_row = QHBoxLayout()
        top_row.setSpacing(5)
        top_row.addWidget(self.add_btn)
        top_row.addWidget(self.reveal_btn)
        self.layout.addLayout(top_row)
        self.empty_label = QLabel("Секретных вопросов нет")
        self.layout.addWidget(self.empty_label)
        self.layout.addStretch()
        self._update_empty()

    def _secret_edits(self):
        return [a for _q, a, _btn, _w in self.rows]

    @staticmethod
    def _row_is_empty(row):
        return not (row[0].text().strip() or row[1].text().strip())

    def _answers_hidden(self) -> bool:
        return self._secrets_hidden()

    MAX_ROWS = 5

    def add_row(self, q="", a="", enforce_limit=True):
        """Лимит MAX_ROWS действует только для ручного добавления; загрузка
        сохранённых данных (set_data) не усекается — иначе следующее
        сохранение удалило бы лишние строки."""
        if enforce_limit and len(self.rows) >= self.MAX_ROWS:
            _warn(self.config, self, "Лимит", "Максимум 5 вопросов!")
            return

        row_widget = QWidget(self)
        h_layout = QHBoxLayout(row_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(5)

        q_edit = QLineEdit("" if q is None else str(q))
        q_edit.setPlaceholderText("Секретный вопрос...")
        q_edit.setReadOnly(not self._editable)
        a_edit = QLineEdit("" if a is None else str(a))
        a_edit.setPlaceholderText("Ответ...")
        a_edit.setReadOnly(not self._editable)
        if self._answers_hidden():
            a_edit.setEchoMode(QLineEdit.Password)

        del_btn = make_delete_button(row_widget, "Удалить вопрос")
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
        self._remove_row_widget(row_widget)
        self._update_empty()

    def get_data(self):
        # Не сохраняем строку, если оба связанных поля пустые.
        return [{"q": q.text(), "a": a.text()} for q, a, _btn, _w in self.rows
                if q.text().strip() or a.text().strip()]

    def set_data(self, data):
        with updates_suspended(self):
            self._clear_rows()
            for item in data:
                self.add_row(item.get("q", ""), item.get("a", ""),
                             enforce_limit=False)
            self._update_empty()

    def set_editable(self, editable):
        self._editable = editable
        self.add_btn.setVisible(editable)
        for q, a, del_btn, _w in self.rows:
            q.setReadOnly(not editable)
            a.setReadOnly(not editable)
            del_btn.setVisible(editable)
        self._finish_set_editable(editable)
