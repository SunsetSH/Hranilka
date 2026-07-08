"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.domain.
Удаляется на этапе 7."""
import sys
from hranilka import domain as _real
sys.modules[__name__] = _real
