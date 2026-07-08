"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.ui.flowlayout.
Удаляется на этапе 7."""
import sys
from hranilka.ui import flowlayout as _real
sys.modules[__name__] = _real
