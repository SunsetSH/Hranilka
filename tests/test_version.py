"""Версия приложения: единый источник hranilka.__version__ (его читает
Hranilka.spec при сборке exe)."""
import re

import hranilka


def test_version_format():
    # 1–4 числа через точку: "1.1", "1.1.0" и т.п. Опечатка здесь сломала бы
    # сборку exe (spec парсит строку в кортеж из int).
    assert re.fullmatch(r"\d+(\.\d+){0,3}", hranilka.__version__)


def test_version_converts_to_four_part_tuple():
    # Та же конверсия, что в Hranilka.spec: "1.5" -> (1, 5, 0, 0).
    ver4 = tuple((list(map(int, hranilka.__version__.split("."))) + [0, 0, 0])[:4])
    assert len(ver4) == 4 and all(0 <= n <= 65535 for n in ver4)
