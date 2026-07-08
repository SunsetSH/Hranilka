"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.ui.account_card.
Удаляется на этапе 7."""
import sys
from hranilka.ui import account_card as _real
sys.modules[__name__] = _real
