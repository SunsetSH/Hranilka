"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.crypto.store.
Удаляется на этапе 7."""
import sys
from hranilka.crypto import store as _real
sys.modules[__name__] = _real
