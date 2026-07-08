"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.data.backup.
Удаляется на этапе 7."""
import sys
from hranilka.data import backup as _real
sys.modules[__name__] = _real
