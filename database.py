"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.data.database.
Удаляется на этапе 7."""
import sys
from hranilka.data import database as _real
sys.modules[__name__] = _real
