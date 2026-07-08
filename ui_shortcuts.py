"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.ui.shortcuts_mixin.
Удаляется на этапе 7."""
import sys
from hranilka.ui import shortcuts_mixin as _real
sys.modules[__name__] = _real
