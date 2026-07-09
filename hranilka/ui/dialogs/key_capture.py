"""Диалог захвата сочетания клавиш (вынесен из dialogs.py, этап 4)."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QLabel

from hranilka.core import shortcuts
from hranilka.ui.theme import ThemedDialog


class KeyCaptureDialog(ThemedDialog):
    """Модальное окно захвата сочетания клавиш. Ловит реальное нажатие через
    keyPressEvent: Esc — отмена, Backspace — снять сочетание. При конфликте с
    другим действием назначение блокируется и показывается предупреждение."""

    _MOD_MASK = (Qt.ControlModifier | Qt.ShiftModifier
                 | Qt.AltModifier | Qt.MetaModifier)
    _MODIFIER_KEYS = {Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta,
                      Qt.Key_AltGr, Qt.Key_CapsLock, Qt.Key_NumLock,
                      Qt.Key_ScrollLock}

    def __init__(self, config, working, sid, parent=None):
        super().__init__(config, parent)
        self._working = working
        self._sid = sid
        self.result_sequence = working.get(sid, "")
        self.setWindowTitle("Назначение клавиши")
        self.setModal(True)
        self.setMinimumWidth(420)

        lay = self.body
        lay.addWidget(QLabel(f"Действие: {shortcuts.LABELS.get(sid, sid)}"))
        self._prompt = QLabel("Нажмите сочетание клавиш…\n"
                              "Esc — отмена, Backspace — снять сочетание.")
        self._prompt.setWordWrap(True)
        lay.addWidget(self._prompt)

    @staticmethod
    def _norm(seq):
        return QKeySequence(seq).toString() if seq else ""

    def keyPressEvent(self, e):
        key = e.key()
        mods = e.modifiers() & self._MOD_MASK
        if key == Qt.Key_Escape and mods == Qt.NoModifier:
            self.reject()
            return
        if key == Qt.Key_Backspace and mods == Qt.NoModifier:
            self.result_sequence = ""
            self.accept()
            return
        if key in self._MODIFIER_KEYS:
            return                       # одиночный модификатор — ждём дальше
        # Буквы/цифры записываем латиницей по физической клавише
        # (nativeVirtualKey не зависит от раскладки: VK A–Z = 0x41–0x5A,
        # 0–9 = 0x30–0x39). Для остальных клавиш (F1, Del…) берём e.key().
        vk = e.nativeVirtualKey()
        key_code = vk if (0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39) else key
        candidate = QKeySequence(int(mods.value) | int(key_code)).toString()
        if not candidate:
            return
        # Конфликт: блокируем и подсвечиваем
        for other_sid, other_seq in self._working.items():
            if other_sid != self._sid and self._norm(other_seq) == candidate:
                self._prompt.setText(
                    f"Сочетание «{candidate}» уже назначено действию "
                    f"«{shortcuts.LABELS.get(other_sid, other_sid)}». "
                    f"Выберите другое.")
                # Акцент ошибки — самосогласованная пара фон+текст (белый на
                # кирпично-красном): читаема на любой теме, в отличие от
                # прежнего красного текста поверх фона темы (H-10).
                self._prompt.setStyleSheet(
                    "background-color: #C0392B; color: #FFFFFF;"
                    " font-weight: bold; padding: 2px;")
                return
        self.result_sequence = candidate
        self.accept()

