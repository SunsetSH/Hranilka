"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.ui.welcome.
Удаляется на этапе 7."""
import sys
from hranilka.ui import welcome as _real
sys.modules[__name__] = _real
