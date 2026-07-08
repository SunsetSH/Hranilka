"""Общие помощники виджетов: темизированные предупреждение/подтверждение,
заголовок поля, ограниченное чтение файла (вынесены из widgets.py, этап 5)."""
import os
from PySide6.QtWidgets import (QLabel, QMessageBox)
from hranilka.ui.theme import themed_info, themed_confirm


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

