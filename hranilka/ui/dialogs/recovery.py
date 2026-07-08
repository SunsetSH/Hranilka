"""Диалог показа recovery-кода (вынесен из dialogs.py, этап 4)."""
from PySide6.QtWidgets import QDialog

from hranilka.ui.theme import ThemedDialog, themed_confirm


class RecoveryCodeDialog(ThemedDialog):
    """Диалог показа recovery-кода. Код показывается ОДИН РАЗ, поэтому закрытие
    без подтверждения «Я сохранил код» (крестик, Esc, Alt+F4) переспрашивает
    (L-3) — случайно потерять код нельзя."""

    def _confirm_discard(self):
        return themed_confirm(self.config, self, "Recovery-код",
                              "Код не сохранён. Закрыть без сохранения?")

    def reject(self):
        if self._confirm_discard():
            super().reject()

    def closeEvent(self, e):
        # Alt+F4/системное закрытие идёт мимо reject() — тоже переспрашиваем.
        if self.result() == QDialog.Accepted or self._confirm_discard():
            super().closeEvent(e)
        else:
            e.ignore()

