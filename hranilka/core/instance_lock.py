"""Межпроцессная блокировка файла-БД (vault), чтобы два экземпляра «Хранилки»
не перезаписывали изменения друг друга.

Зачем: данные хранятся как один файл, который при сохранении целиком заменяется
(`os.replace`). Блокировки SQLite на это не распространяются — два процесса,
открывшие один файл, затёрли бы правки друг друга по принципу last-writer-wins.

Реализация (Windows): лок-файл рядом с БД, удерживаемый эксклюзивной блокировкой
байтового диапазона через `msvcrt.locking`. Ключевое свойство — ОС снимает такую
блокировку при завершении процесса, в том числе аварийном, поэтому «протухших»
локов после краша не остаётся (в отличие от схемы «существует файл = занято»)."""

import logging
import os
import socket

try:                     # Windows: байтовая блокировка
    import msvcrt
except ImportError:      # не-Windows (тесты/линт на другой ОС)
    msvcrt = None        # type: ignore[assignment]
    try:
        import fcntl
    except ImportError:
        fcntl = None     # type: ignore[assignment]

# Байт 0 лок-файла - объект блокировки; сведения о владельце (PID/хост) пишутся
# со смещения 1, чтобы запись не конфликтовала с заблокированным диапазоном.
_INFO_OFFSET = 1
_INFO_MAX = 256


class VaultLockedError(Exception):
    """Файл-БД уже открыт другим экземпляром «Хранилки».

    holder - строка "pid=... host=..." владельца блокировки (если удалось
    прочитать), иначе пустая."""

    def __init__(self, lock_path="", holder=""):
        super().__init__(lock_path)
        self.holder = holder


def _try_lock(fd) -> None:
    """Неблокирующий захват байта 0. OSError - занято/не поддерживается."""
    if msvcrt is not None:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    elif fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        logging.warning("Блокировка экземпляров недоступна на этой платформе.")


def _unlock(fd) -> None:
    if msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    elif fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _read_holder(fd) -> str:
    """Best-effort: прочитать строку владельца из лок-файла."""
    try:
        os.lseek(fd, _INFO_OFFSET, os.SEEK_SET)
        return os.read(fd, _INFO_MAX).decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _write_holder(fd) -> None:
    """Best-effort: записать PID/хост (диагностика «кто держит файл»)."""
    try:
        info = f"pid={os.getpid()} host={socket.gethostname()}".encode("utf-8")
        os.lseek(fd, _INFO_OFFSET, os.SEEK_SET)
        os.write(fd, info)
        os.ftruncate(fd, _INFO_OFFSET + len(info))
    except OSError as e:
        logging.debug("Не удалось записать сведения о владельце лока: %s", e)


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
            # Блокируем 1 байт от текущей позиции (0), без ожидания.
            os.lseek(fd, 0, os.SEEK_SET)
            _try_lock(fd)
        except OSError:
            holder = _read_holder(fd)
            os.close(fd)
            raise VaultLockedError(self.lock_path, holder)
        _write_holder(fd)
        self._fd = fd

    def release(self):
        """Снять блокировку и закрыть дескриптор (идемпотентно)."""
        if self._fd is None:
            return
        try:
            os.ftruncate(self._fd, 0)       # владельца больше нет
        except OSError:
            pass
        try:
            _unlock(self._fd)
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
