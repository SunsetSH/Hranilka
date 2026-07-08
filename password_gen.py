"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.generators.password_gen.
Удаляется на этапе 7."""
import sys
from hranilka.generators import password_gen as _real
sys.modules[__name__] = _real
