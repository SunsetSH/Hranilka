"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.ui.theme.
Удаляется на этапе 7."""
import sys
from hranilka.ui import theme as _real
sys.modules[__name__] = _real
