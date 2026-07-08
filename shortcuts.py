"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.shortcuts.
Удаляется на этапе 7."""
import sys
from hranilka import shortcuts as _real
sys.modules[__name__] = _real
