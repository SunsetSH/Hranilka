"""Общие помощники виджетов: темизированные предупреждение/подтверждение,
заголовок поля, ограниченное чтение файла (вынесены из widgets.py, этап 5)."""
import os
from contextlib import contextmanager
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QLabel, QMessageBox, QComboBox, QPushButton,
                               QLineEdit, QApplication)
from hranilka.ui.theme import themed_info, themed_confirm


class ReadOnlyAwareComboBox(QComboBox):
    """Редактируемый QComboBox, у которого режим просмотра выглядит как обычное
    поле (не тускнеет, как disabled), но не реагирует на ввод.

    В просмотре: lineEdit только для чтения, фокус снят, а клик/колёсико/клавиши
    игнорируются — вид совпадает с readonly-QLineEdit по яркости текста, но
    значение случайно не меняется (в т.ч. колесом мыши). В правке — обычный
    комбобокс. Взамен setEnabled(False), из-за которого текст приглушался."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._view_only = False

    def set_view_only(self, view_only: bool) -> None:
        self._view_only = view_only
        if self.isEditable():
            self.lineEdit().setReadOnly(view_only)
        self.setFocusPolicy(Qt.NoFocus if view_only else Qt.WheelFocus)

    def wheelEvent(self, event):
        if self._view_only:
            event.ignore()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        if self._view_only:
            event.ignore()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if self._view_only:
            event.ignore()
            return
        super().keyPressEvent(event)


def _warn(config, parent, title, text):
    """Показать предупреждение в стиле программы; при отсутствии config —
    откат на стандартный QMessageBox."""
    if config is not None:
        themed_info(config, parent, title, text)
    else:
        QMessageBox.warning(parent, title, text)


def _confirm(config, parent, title, text):
    """Запрос подтверждения в стиле программы; при отсутствии config — откат
    на стандартный QMessageBox. Возвращает True, если пользователь согласился."""
    if config is not None:
        return themed_confirm(config, parent, title, text)
    return QMessageBox.question(parent, title, text) == QMessageBox.Yes


def heading_label(text):
    """QLabel-заголовок (поля, секции). Кегль на +2 от базового задаётся темой
    через свойство heading."""
    lbl = QLabel(text)
    lbl.setProperty("heading", "true")
    return lbl

class FileTooLargeError(Exception):
    """Файл превысил допустимый размер при ограниченном чтении (L65-02)."""


def _read_file(path, max_bytes):
    """Прочитать файл целиком, но не более max_bytes. Для вызова в фоновом потоке.

    Файл открывается ОДИН раз, а размер проверяется через fstat уже открытого
    дескриптора — без TOCTOU-разрыва между getsize и open. Дополнительно читаем
    не более max_bytes+1 байт: если файл/симлинк подменили на больший уже после
    fstat, лишний байт вскроет это и мы не затянем в память гигабайты (L65-02)."""
    with open(path, "rb") as f:
        if os.fstat(f.fileno()).st_size > max_bytes:
            raise FileTooLargeError(path)
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise FileTooLargeError(path)
    return data



# ─── Общие помощники (аудит 2026-11, раздел 8 / P9 / доступность) ────────────

def set_a11y(widget, name, description=""):
    """Задать accessibleName/Description (кнопки без текстовой подписи:
    [X], [КОП], [*] — скринридер иначе читает «крестик в скобках»)."""
    widget.setAccessibleName(name)
    if description:
        widget.setAccessibleDescription(description)
    return widget


def make_copy_button(parent=None, name="Копировать"):
    """Кнопка [КОП] фиксированной ширины с доступным именем."""
    btn = QPushButton("[КОП]", parent) if parent is not None else QPushButton("[КОП]")
    btn.setFixedWidth(60)
    set_a11y(btn, name)
    return btn


def make_delete_button(parent, name="Удалить"):
    """Кнопка [X] фиксированной ширины (родитель задаётся сразу — иначе
    setVisible() на виджете без родителя открывает отдельное окно)."""
    btn = QPushButton("[X]", parent)
    btn.setFixedWidth(40)
    set_a11y(btn, name)
    return btn


@contextmanager
def updates_suspended(widget):
    """Отключить перерисовку виджета на время массовой пересборки (P9):
    без мерцания; перерисовка гарантированно возвращается (finally)."""
    was_enabled = widget.updatesEnabled()
    widget.setUpdatesEnabled(False)
    try:
        yield
    finally:
        widget.setUpdatesEnabled(was_enabled)


def copy_secret(text):
    """Скопировать секрет через канал программы с автоочисткой буфера
    (ui.chrome.copy_secret_to_clipboard). Импорт ленивый — избегаем цикла
    chrome <-> widgets; если функции ещё нет, откат на обычный буфер."""
    try:
        from hranilka.ui.chrome import copy_secret_to_clipboard
    except ImportError:
        QApplication.clipboard().setText(text)
        return
    copy_secret_to_clipboard(text)


class RevealableRowsMixin:
    """Общий каркас списков со скрываемым секретом (коды 2FA, секретные
    вопросы): пустая метка, маска echo, кнопка «показать», отсев пустых строк.

    Хост задаёт: self.rows, self.layout, self._editable, self._revealed,
    self.reveal_btn, self.empty_label; REVEAL_SHOW/REVEAL_HIDE; и хуки
    _secret_edits(), _row_widget(row), _row_is_empty(row)."""

    REVEAL_SHOW = "ПОКАЗАТЬ"
    REVEAL_HIDE = "СКРЫТЬ"

    def _secret_edits(self):
        raise NotImplementedError

    @staticmethod
    def _row_widget(row):
        return row[-1]

    @staticmethod
    def _row_is_empty(row):
        raise NotImplementedError

    def _update_empty(self):
        self.empty_label.setVisible(not self.rows)
        # Кнопка показа осмысленна только в просмотре и при наличии строк.
        self.reveal_btn.setVisible(bool(self.rows) and not self._editable)

    def _secrets_hidden(self) -> bool:
        return not (self._editable or self._revealed)

    def _apply_echo(self):
        mode = QLineEdit.Password if self._secrets_hidden() else QLineEdit.Normal
        for edit in self._secret_edits():
            edit.setEchoMode(mode)

    def _on_reveal_toggled(self, checked: bool):
        self._revealed = checked
        self.reveal_btn.setText(self.REVEAL_HIDE if checked else self.REVEAL_SHOW)
        self._apply_echo()

    def _remove_row_widget(self, row_widget):
        self.layout.removeWidget(row_widget)
        row_widget.deleteLater()

    def _prune_empty_rows(self):
        """Пустые строки не сохраняются в БД (см. get_data) — после выхода из
        правки убираем их и из интерфейса, не дожидаясь перезагрузки."""
        drop = [r for r in self.rows if self._row_is_empty(r)]
        if not drop:
            return
        self.rows = [r for r in self.rows if not self._row_is_empty(r)]
        for r in drop:
            self._remove_row_widget(self._row_widget(r))
        self._update_empty()

    def _finish_set_editable(self, editable):
        """Хвост set_editable: выход из правки чистит пустое и маскирует секрет,
        вход — показывает для ввода."""
        if not editable:
            self._prune_empty_rows()
        self._revealed = False
        self.reveal_btn.setChecked(False)
        self._apply_echo()
        self._update_empty()

    def _clear_rows(self):
        for r in self.rows:
            self._remove_row_widget(self._row_widget(r))
        self.rows.clear()
