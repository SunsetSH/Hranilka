"""Версия приложения: единый источник hranilka.__version__ (его читает
Hranilka.spec при сборке exe)."""
import re

import hranilka


def test_version_format():
    # 1–4 числа через точку: "1.1", "1.1.0" и т.п. Опечатка здесь сломала бы
    # сборку exe (spec парсит строку в кортеж из int).
    assert re.fullmatch(r"\d+(\.\d+){0,3}", hranilka.__version__)
