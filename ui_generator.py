"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.ui.generator_dialog.
Удаляется на этапе 7."""
import sys
from hranilka.ui import generator_dialog as _real
sys.modules[__name__] = _real
