"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.generators.pd_data.
Удаляется на этапе 7."""
import sys
from hranilka.generators import pd_data as _real
sys.modules[__name__] = _real
