"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.ui.vault_controller.
Удаляется на этапе 7."""
import sys
from hranilka.ui import vault_controller as _real
sys.modules[__name__] = _real
