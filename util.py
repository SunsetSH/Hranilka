"""Небольшие утилиты общего назначения."""

import secrets
import string

# Набор символов для генерируемых паролей (буквы, цифры, безопасные спецсимволы).
_PASSWORD_CHARS = string.ascii_letters + string.digits + "!@#$%^&*"


def generate_password(length: int = 16) -> str:
    """Криптостойкий случайный пароль (на secrets, не random)."""
    return "".join(secrets.choice(_PASSWORD_CHARS) for _ in range(length))
