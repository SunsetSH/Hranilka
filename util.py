"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.util.
Удаляется на этапе 7."""
import sys
from hranilka import util as _real
sys.modules[__name__] = _real
