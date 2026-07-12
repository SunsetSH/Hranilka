"""SeedPhraseWidget — seed-фраза криптокошелька (концепт §6): сетка нумерованных
ячеек (3 колонки), комбо количества слов 12/15/18/21/24 перестраивает сетку.

По умолчанию слова скрыты (эхо-режим пароля); «ПОКАЗАТЬ ВСЁ» — reveal с
автоскрытием по таймеру; «КОПИРОВАТЬ ФРАЗУ» — вся фраза одной строкой через
copy_signal (автоочистку буфера подхватывает MainWindow); в правке —
«ВСТАВИТЬ ФРАЗУ» (парсинг буфера по пробелам/переносам). Слово не из словаря
BIP-39 помечается маркером [!] — предупреждение, не блокировка.

Контракт совпадает с CopyableField (set_text/get_text/set_editable/copy_signal):
фабрика FinItemTabs строит виджет единообразно; значение в payload — строка
(слова через пробел).
"""
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QLineEdit, QPushButton, QLabel, QComboBox,
                               QApplication)
from PySide6.QtCore import Signal, QTimer

from hranilka.core.bip39_words import BIP39_WORDS

# Допустимые размеры фразы (BIP-39), колонок в сетке и таймаут автоскрытия.
WORD_COUNTS = (12, 15, 18, 21, 24)
_COLUMNS = 3
REVEAL_TIMEOUT_MS = 30_000


class SeedPhraseWidget(QWidget):
    """Сетка ячеек seed-фразы с reveal/копированием/вставкой и BIP-39 проверкой."""

    copy_signal = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editable = False
        self._revealed = False
        # Ячейки: (QLineEdit слова, QLabel-маркер [!], контейнер ячейки).
        self._cells: list[tuple[QLineEdit, QLabel, QWidget]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        layout.addLayout(self._build_toolbar())

        self._grid = QGridLayout()
        self._grid.setSpacing(5)
        layout.addLayout(self._grid)
        layout.addStretch()

        # Автоскрытие показанной фразы (секрет не должен «зависать» на экране).
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(REVEAL_TIMEOUT_MS)
        self._hide_timer.timeout.connect(lambda: self._set_revealed(False))

        self._rebuild_grid(WORD_COUNTS[0], [])
        self.set_editable(False)

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(5)
        row.addWidget(QLabel("Слов:"))
        self.count_combo = QComboBox()
        for n in WORD_COUNTS:
            self.count_combo.addItem(str(n), n)
        self.count_combo.currentIndexChanged.connect(self._on_count_changed)
        row.addWidget(self.count_combo)
        row.addStretch()

        self.reveal_btn = QPushButton("ПОКАЗАТЬ ВСЁ")
        self.reveal_btn.setToolTip(
            f"Показать фразу (автоскрытие через {REVEAL_TIMEOUT_MS // 1000} с)")
        self.reveal_btn.clicked.connect(
            lambda: self._set_revealed(not self._revealed))
        row.addWidget(self.reveal_btn)

        self.copy_btn = QPushButton("КОПИРОВАТЬ ФРАЗУ")
        self.copy_btn.clicked.connect(self.do_copy)
        row.addWidget(self.copy_btn)

        self.paste_btn = QPushButton("ВСТАВИТЬ ФРАЗУ")
        self.paste_btn.setToolTip("Разобрать фразу из буфера обмена по ячейкам")
        self.paste_btn.clicked.connect(self.paste_phrase)
        row.addWidget(self.paste_btn)
        return row

    # ----- Контракт поля -----

    def set_text(self, text: str) -> None:
        """Фраза строкой (слова через пробел/перенос) → ячейки. Размер сетки —
        минимальный допустимый, вмещающий все слова (лишние отбрасываются)."""
        words = (text or "").split()
        count = next((n for n in WORD_COUNTS if n >= len(words)),
                     WORD_COUNTS[-1])
        self._set_count_silently(count)
        self._rebuild_grid(count, words)

    def get_text(self) -> str:
        """Фраза одной строкой: непустые слова через пробел."""
        return " ".join(w for w in self._words() if w)

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self.count_combo.setEnabled(editable)
        self.paste_btn.setVisible(editable)
        # Просмотр: reveal и копирование; правка: слова видны (как у пароля).
        self.reveal_btn.setVisible(not editable)
        self.copy_btn.setVisible(not editable)
        if editable:
            self._hide_timer.stop()
        else:
            self._revealed = False
            self.reveal_btn.setText("ПОКАЗАТЬ ВСЁ")
        for edit, _warn, _cell in self._cells:
            edit.setReadOnly(not editable)
        self._apply_echo()

    def do_copy(self) -> None:
        """Скопировать фразу одной строкой (канал с автоочисткой буфера)."""
        text = self.get_text()
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()

    def paste_phrase(self) -> None:
        """Разобрать текст буфера по пробелам/переносам в ячейки (только правка)."""
        text = QApplication.clipboard().text()
        if text and text.split():
            self.set_text(text)

    # ----- Внутреннее -----

    def _words(self) -> list[str]:
        return [edit.text().strip() for edit, _warn, _cell in self._cells]

    def _set_count_silently(self, count: int) -> None:
        """Выставить комбо программно, не триггеря повторную перестройку."""
        idx = self.count_combo.findData(count)
        self.count_combo.blockSignals(True)
        self.count_combo.setCurrentIndex(idx)
        self.count_combo.blockSignals(False)

    def _on_count_changed(self, _index: int) -> None:
        """Смена количества слов пользователем: перестроить сетку, сохранив
        введённые слова (лишние — отбрасываются)."""
        count = self.count_combo.currentData()
        self._rebuild_grid(int(count), self._words())

    def _rebuild_grid(self, count: int, words: list[str]) -> None:
        for _edit, _warn, cell in self._cells:
            self._grid.removeWidget(cell)
            cell.deleteLater()
        self._cells.clear()
        for i in range(count):
            word = words[i] if i < len(words) else ""
            self._grid.addWidget(self._make_cell(i, word),
                                 i // _COLUMNS, i % _COLUMNS)
        self._apply_echo()

    def _make_cell(self, index: int, word: str) -> QWidget:
        """Ячейка «N. [слово] [!]»: маркер [!] — слово не из словаря BIP-39."""
        cell = QWidget(self)
        h = QHBoxLayout(cell)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(3)
        num = QLabel(f"{index + 1}.")
        num.setFixedWidth(24)
        h.addWidget(num)
        edit = QLineEdit(word)
        edit.setReadOnly(not self._editable)
        h.addWidget(edit, 1)
        warn = QLabel("[!]")
        warn.setToolTip("Слово не из словаря BIP-39")
        h.addWidget(warn)
        edit.textChanged.connect(
            lambda _t, e=edit, w=warn: self._update_bip39_warn(e, w))
        self._update_bip39_warn(edit, warn)
        self._cells.append((edit, warn, cell))
        return cell

    @staticmethod
    def _update_bip39_warn(edit: QLineEdit, warn: QLabel) -> None:
        word = edit.text().strip().lower()
        warn.setVisible(bool(word) and word not in BIP39_WORDS)

    def _set_revealed(self, revealed: bool) -> None:
        """Показ/скрытие слов в просмотре; показ — с таймером автоскрытия."""
        self._revealed = revealed
        self.reveal_btn.setText("СКРЫТЬ" if revealed else "ПОКАЗАТЬ ВСЁ")
        if revealed:
            self._hide_timer.start()
        else:
            self._hide_timer.stop()
        self._apply_echo()

    def _apply_echo(self) -> None:
        """Эхо-режим ячеек: слова видны в правке и при reveal, иначе скрыты."""
        visible = self._editable or self._revealed
        mode = QLineEdit.Normal if visible else QLineEdit.Password
        for edit, _warn, _cell in self._cells:
            edit.setEchoMode(mode)
