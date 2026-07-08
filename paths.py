"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.paths.
Удаляется на этапе 7."""
import sys
from hranilka import paths as _real
sys.modules[__name__] = _real
