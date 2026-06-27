"""Межпроцессная блокировка файла-БД (vault), чтобы два экземпляра «Хранилки»
не перезаписывали изменения друг друга.

Зачем: данные хранятся как один файл, который при сохранении целиком заменяется
(`os.replace`). Блокировки SQLite на это не распространяются — два процесса,
открывшие один файл, затёрли бы правки друг друга по принципу last-writer-wins.

Реализация (Windows): лок-файл рядом с БД, удерживаемый эксклюзивной блокировкой
байтового диапазона через `msvcrt.locking`. Ключевое свойство — ОС снимает такую
блокировку при завершении процесса, в том числе аварийном, поэтому «протухших»
локов после краша не остаётся (в отличие от схемы «существует файл = занято»)."""

import os
import msvcrt


class VaultLockedError(Exception):
    """Файл-БД уже открыт другим экземпляром «Хранилки»."""


class InstanceLock:
    """Удержание эксклюзивной блокировки на время работы процесса.

    Захватывается один раз при старте и снимается при выходе. Авто-блокировка по
    простою (lock vault) блокировку НЕ снимает — файл по-прежнему наш."""

    def __init__(self, db_path):
        self.lock_path = str(db_path) + ".lock"
        self._fd = None

    def acquire(self):
        """Захватить блокировку. VaultLockedError, если файл уже занят другим
        экземпляром. Прочие ошибки (нет доступа к каталогу) пробрасываются."""
        if self._fd is not None:
            return
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT)
        try:
            # Блокируем 1 байт от текущей позиции (0). LK_NBLCK — без ожидания.
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(fd)
            raise VaultLockedError(self.lock_path)
        self._fd = fd

    def release(self):
        """Снять блокировку и закрыть дескриптор (идемпотентно)."""
        if self._fd is None:
            return
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
