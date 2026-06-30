"""Небольшие утилиты общего назначения."""

import asyncio
import logging
import secrets
import string

# Набор символов для генерируемых паролей (буквы, цифры, безопасные спецсимволы).
_PASSWORD_CHARS = string.ascii_letters + string.digits + "!@#$%^&*"


def generate_password(length: int = 16) -> str:
    """Криптостойкий случайный пароль (на secrets, не random)."""
    return "".join(secrets.choice(_PASSWORD_CHARS) for _ in range(length))


def _report_task_error(task):
    if not task.cancelled() and task.exception() is not None:
        logging.error("Ошибка async-операции: %s", task.exception(),
                      exc_info=task.exception())


def fire(coro):
    """Запустить корутину «выстрелил-и-забыл».

    Под работающим event-loop (qasync, пока открыто окно) — планирует Task и
    возвращает его; исключения не теряются (ensure_future глотает их молча) —
    логируем их в done-callback. Если активного цикла нет (тесты/скрипты без
    qasync), выполняет корутину СИНХРОННО до конца, сохраняя прежнее поведение."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        task = asyncio.ensure_future(coro)
        task.add_done_callback(_report_task_error)
        return task
    new_loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(new_loop)
        return new_loop.run_until_complete(coro)
    finally:
        asyncio.set_event_loop(None)
        new_loop.close()
