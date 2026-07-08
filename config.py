"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.config.
Удаляется на этапе 7."""
import sys
from hranilka import config as _real
sys.modules[__name__] = _real
