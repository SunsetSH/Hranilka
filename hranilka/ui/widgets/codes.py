"""Виджет резервных кодов 2FA с импортом из текстового файла
(вынесен из widgets.py, этап 5)."""
import logging
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton, QLabel, QFileDialog, QApplication)
from PySide6.QtCore import Signal
from hranilka.ui.widgets.common import (FileTooLargeError, _confirm,
                                        _read_file, _warn)


class CodeListWidget(QWidget):
    copy_signal = Signal()

    # Лимиты импорта из файла: список кодов заведомо мал, всё сверх — защита
    # от случайно выбранного «не того» файла (OOM / тысячи виджетов).
    _MAX_IMPORT_BYTES = 1024 * 1024
    _MAX_IMPORT_CODES = 100

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(5)
        self.rows = []
        self._editable = False
        self._revealed = False

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(5)
        self.add_btn = QPushButton("+ ДОБАВИТЬ КОД")
        self.add_btn.clicked.connect(lambda: self.add_code())
        btn_layout.addWidget(self.add_btn)
        self.import_btn = QPushButton("ИМПОРТ")
        self.import_btn.clicked.connect(self.import_codes)
        btn_layout.addWidget(self.import_btn)
        self.reveal_btn = QPushButton("ПОКАЗАТЬ КОДЫ")
        self.reveal_btn.setCheckable(True)
        self.reveal_btn.setToolTip("Показать / скрыть все коды")
        self.reveal_btn.toggled.connect(self._on_reveal_toggled)
        btn_layout.addWidget(self.reveal_btn)
        self.layout.addLayout(btn_layout)

        self.import_hint = QLabel(
            "Импорт: текстовый документ (.txt), в котором коды разделены "
            "новой строкой (абзацем) — один код станет одним полем.")
        self.import_hint.setWordWrap(True)
        self.layout.addWidget(self.import_hint)

        self.empty_label = QLabel("Резервных кодов нет")
        self.layout.addWidget(self.empty_label)
        self.layout.addStretch()
        self._update_empty()

    def _update_empty(self):
        self.empty_label.setVisible(not self.rows)
        # Кнопка показа осмысленна только в просмотре и при наличии строк.
        self.reveal_btn.setVisible(bool(self.rows) and not self._editable)

    def _codes_hidden(self) -> bool:
        return not (self._editable or self._revealed)

    def _apply_echo(self):
        mode = QLineEdit.Password if self._codes_hidden() else QLineEdit.Normal
        for code_edit, _btn, _w in self.rows:
            code_edit.setEchoMode(mode)

    def _on_reveal_toggled(self, checked: bool):
        self._revealed = checked
        self.reveal_btn.setText("СКРЫТЬ КОДЫ" if checked else "ПОКАЗАТЬ КОДЫ")
        self._apply_echo()

    def add_code(self, code_text=""):
        row_widget = QWidget(self)
        h_layout = QHBoxLayout(row_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(5)

        code_edit = QLineEdit(code_text)
        code_edit.setReadOnly(not self._editable)
        code_edit.setPlaceholderText("Код / резервный ключ...")
        if self._codes_hidden():
            code_edit.setEchoMode(QLineEdit.Password)
        h_layout.addWidget(code_edit)
        
        copy_btn = QPushButton("[КОП]", row_widget)  # родитель сразу — см. add_item
        copy_btn.setFixedWidth(60)
        copy_btn.clicked.connect(lambda: self.copy_code(code_edit.text()))
        h_layout.addWidget(copy_btn)

        del_btn = QPushButton("[X]", row_widget)     # родитель сразу — см. add_item
        del_btn.setFixedWidth(40)
        del_btn.clicked.connect(lambda: self.remove_code(row_widget))
        h_layout.addWidget(del_btn)
        
        self.layout.insertWidget(self.layout.count() - 2, row_widget)
        # Прямая ссылка на кнопку удаления (L-9) — без layout().itemAt(...).
        self.rows.append((code_edit, del_btn, row_widget))
        self._update_empty()

    def import_codes(self):
        """Импорт кодов из текстового файла: одна непустая строка — одно поле."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт резервных кодов", "",
            "Текстовые файлы (*.txt);;Все файлы (*)")
        if not path:
            return
        try:
            data = _read_file(path, self._MAX_IMPORT_BYTES)
        except FileTooLargeError:
            _warn(self.config, self, "Слишком большой файл",
                  f"Файл больше {self._MAX_IMPORT_BYTES // (1024 * 1024)} МБ — "
                  f"это не похоже на список кодов.")
            return
        except OSError as e:
            logging.warning("Импорт кодов: не удалось прочитать %s: %s", path, e)
            _warn(self.config, self, "Ошибка",
                  f"Не удалось прочитать файл:\n{e}")
            return
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Файл из старого блокнота/Windows — пробуем системную кириллицу.
            text = data.decode("cp1251", errors="replace")
        codes = [line.strip() for line in text.splitlines() if line.strip()]
        if not codes:
            _warn(self.config, self, "Импорт",
                  "В файле не найдено ни одного кода.")
            return
        if len(codes) > self._MAX_IMPORT_CODES:
            _warn(self.config, self, "Импорт",
                  f"В файле {len(codes)} строк — больше лимита "
                  f"{self._MAX_IMPORT_CODES}. Импорт отменён.")
            return
        for code in codes:
            self.add_code(code)

    def copy_code(self, text):
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()

    def remove_code(self, row_widget):
        if not _confirm(self.config, self, "Удаление",
                        "Удалить этот код / резервный ключ?"):
            return
        for i, (_edit, _btn, widget) in enumerate(self.rows):
            if widget == row_widget:
                self.rows.pop(i)
                break
        self.layout.removeWidget(row_widget)
        row_widget.deleteLater()
        self._update_empty()

    def get_data(self):
        # Не сохраняем пустые коды.
        return [edit.text() for edit, _btn, _w in self.rows if edit.text().strip()]

    def set_data(self, data):
        for _edit, _btn, widget in self.rows:
            self.layout.removeWidget(widget)
            widget.deleteLater()
        self.rows.clear()
        for code in data: self.add_code(code)
        self._update_empty()

    def _prune_empty_rows(self):
        """Пустые коды не сохраняются в БД (см. get_data) — после выхода из
        правки убираем их и из интерфейса, не дожидаясь перезагрузки карточки."""
        drop = [row for row in self.rows if not row[0].text().strip()]
        if not drop:
            return
        self.rows = [row for row in self.rows if row[0].text().strip()]
        for _edit, _btn, widget in drop:
            self.layout.removeWidget(widget)
            widget.deleteLater()
        self._update_empty()

    def set_editable(self, editable):
        self._editable = editable
        self.add_btn.setVisible(editable)
        self.import_btn.setVisible(editable)
        self.import_hint.setVisible(editable)
        for code_edit, del_btn, _w in self.rows:
            code_edit.setReadOnly(not editable)
            del_btn.setVisible(editable)
        if not editable:
            self._prune_empty_rows()
        # Выход из правки снова маскирует коды; вход — показывает для ввода.
        self._revealed = False
        self.reveal_btn.setChecked(False)
        self._apply_echo()
        self._update_empty()
