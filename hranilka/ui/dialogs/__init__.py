"""Диалоги UI (этап 4 реструктуризации): по диалогу на модуль, публичные
имена реэкспортируются здесь."""
# Совместимость переходного периода: тесты патчат dialogs.cs.unlock.
# Убрать на этапе 7 вместе с обновлением тестов.
from hranilka.crypto import store as cs

from hranilka.ui.dialogs.export_dialog import ExportDialog, theme_dict
from hranilka.ui.dialogs.key_capture import KeyCaptureDialog
from hranilka.ui.dialogs.recovery import RecoveryCodeDialog
from hranilka.ui.dialogs.recycle_bin import RecycleBinDialog
from hranilka.ui.dialogs.settings import CODING_FONTS, SettingsDialog
from hranilka.ui.dialogs.unlock import UnlockDialog

__all__ = ["ExportDialog", "KeyCaptureDialog", "RecoveryCodeDialog",
           "RecycleBinDialog", "SettingsDialog", "UnlockDialog",
           "CODING_FONTS", "theme_dict", "cs"]
