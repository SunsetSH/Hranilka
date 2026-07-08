"""Шим переходного периода (этап 1 реструктуризации): реальный модуль — hranilka.ui.chrome.
Удаляется на этапе 7."""
import sys
from hranilka.ui import chrome as _real
sys.modules[__name__] = _real
