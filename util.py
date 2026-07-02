"""Небольшие утилиты общего назначения."""

import asyncio
import logging
import os
import secrets
import string

# Набор символов для генерируемых паролей (буквы, цифры, безопасные спецсимволы).
_PASSWORD_CHARS = string.ascii_letters + string.digits + "!@#$%^&*"


def generate_password(length: int = 16) -> str:
    """Криптостойкий случайный пароль (на secrets, не random)."""
    return "".join(secrets.choice(_PASSWORD_CHARS) for _ in range(length))


def best_effort_wipe(path):
    """Удалить файл, предварительно best-effort затерев его содержимое нулями.

    Применяется к ВРЕМЕННЫМ копиям полной БД (pre-migrate, restore-tmp,
    rollback): такие файлы содержат все пароли/BLOB в открытом виде, и простой
    unlink оставил бы их читаемыми на диске до перезаписи секторов (H-3).
    Перезапись нулями best-effort: если файл не открылся на запись/сбой I/O —
    молча продолжаем к os.remove (это лучше, чем не удалить файл вовсе).
    OSError на самом remove логируется и глотается (как у прежних unlink)."""
    try:
        size = os.path.getsize(path)
        with open(path, "r+b") as f:
            remaining = size
            chunk = 1024 * 1024
            zeros = b"\x00" * chunk
            while remaining > 0:
                n = min(remaining, chunk)
                f.write(zeros[:n])
                remaining -= n
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        # Затирание — best-effort: не смогли открыть/записать — всё равно удаляем.
        logging.warning("Не удалось затереть временный файл %s: %s", path, e)
    try:
        os.remove(path)
    except OSError as e:
        logging.warning("Не удалось удалить временный файл %s: %s", path, e)


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
