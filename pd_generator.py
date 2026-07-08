"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.generators.pd_generator.
Удаляется на этапе 7."""
import sys
from hranilka.generators import pd_generator as _real
sys.modules[__name__] = _real
