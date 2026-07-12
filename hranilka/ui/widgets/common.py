"""Общие помощники виджетов: темизированные предупреждение/подтверждение,
заголовок поля, ограниченное чтение файла (вынесены из widgets.py, этап 5)."""
import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QLabel, QMessageBox, QComboBox)
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

