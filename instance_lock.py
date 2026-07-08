"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.instance_lock.
Удаляется на этапе 7."""
import sys
from hranilka import instance_lock as _real
sys.modules[__name__] = _real
