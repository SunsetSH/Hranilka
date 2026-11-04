"""Установка проверенного обновления: замена exe с откатом. Без Qt.

Windows не даёт перезаписать запущенный exe, но даёт его переименовать, поэтому
вспомогательный bat-файл не нужен. Порядок (выполняется ПОСЛЕ закрытия окна —
БД закрыта, instance-lock снят):

1. снимок hranilka.db и config.json рядом с ними (*.pre-update);
2. Hranilka.exe -> Hranilka.exe.old, загруженный файл -> Hranilka.exe;
3. запуск новой версии с --update-confirm и ожидание файла-метки;
4. метка появилась — старый процесс завершается, .old удалит новая версия;
5. метки нет — новая версия снимается, .old возвращается на место и старая
   версия запускается с --update-failed (показывает сообщение).

Метка пишется до открытия БД: откат не может вернуть старый exe к уже
мигрированной базе. Разбор рисков — docs/Автообновление.md."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from hranilka.core.config import CONFIG_FILE
from hranilka.core.paths import BASE_DIR
from hranilka.services import backup as bk
from hranilka.services import updater
from hranilka.services.updater import StagedUpdate, UpdateError

logger = logging.getLogger(__name__)

CONFIRM_ARG = "--update-confirm"
FAILED_ARG = "--update-failed"
OLD_SUFFIX = ".old"
CONFIRM_MARKER = "Hranilka.update-ok"
SNAPSHOT_SUFFIX = ".pre-update"

_CONFIRM_TIMEOUT_SECS = 90.0     # распаковка onefile + проверка антивирусом
_POLL_SECS = 0.2
_REPLACE_RETRIES = 25            # антивирус держит свежий exe до нескольких секунд
_REPLACE_DELAY_SECS = 0.4
_SNAPSHOT_MAX_AGE_SECS = 14 * 24 * 3600

_pending: Optional[StagedUpdate] = None
_startup_state: Optional[str] = None       # "updated" | "failed" | None


def request_install(staged: StagedUpdate) -> None:
    """Запомнить обновление: установка начнётся после выхода из цикла событий."""
    global _pending
    _pending = staged


def cancel_install() -> None:
    """Пользователь не закрыл программу: файл загрузки больше не нужен."""
    global _pending
    if _pending is not None:
        updater.discard(_pending.path)
        _pending = None


def confirm_startup(argv: list[str]) -> None:
    """Вызвать при запуске до MainWindow: Python, импорты и Qt уже поднялись,
    значит сборка рабочая — сообщаем об этом ожидающей старой версии."""
    global _startup_state
    if FAILED_ARG in argv:
        _startup_state = "failed"
    elif CONFIRM_ARG in argv:
        _startup_state = "updated"
        marker = updater.running_exe().with_name(CONFIRM_MARKER)
        try:
            marker.write_bytes(b"")
        except OSError as e:
            logger.error("Обновление: не удалось записать метку: %s", e)


def take_startup_state() -> Optional[str]:
    """Итог обновления для этого запуска; читается один раз."""
    global _startup_state
    state, _startup_state = _startup_state, None
    return state


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.info("Обновление: %s пока не удалён: %s", path.name, e)


def remove_old_exe() -> None:
    """Удалить прежний exe. Он может ещё выполняться — тогда при следующем
    запуске."""
    if getattr(sys, "frozen", False):
        exe = updater.running_exe()
        _remove(exe.with_name(exe.name + OLD_SUFFIX))


def _remove_stale_snapshots(now: float) -> None:
    for name in (_db_path().name, CONFIG_FILE.name):
        path = BASE_DIR / (name + SNAPSHOT_SUFFIX)
        try:
            if now - path.stat().st_mtime > _SNAPSHOT_MAX_AGE_SECS:
                _remove(path)
        except OSError:
            continue


def cleanup_leftovers() -> None:
    """Убрать хвосты прошлых обновлений. Снимок хранится 14 дней: в нём данные
    на момент обновления, бессрочно копить их незачем."""
    if not getattr(sys, "frozen", False):
        return
    exe = updater.running_exe()
    for name in (exe.name + OLD_SUFFIX, CONFIRM_MARKER,
                 updater.ASSET_EXE + updater.STAGED_SUFFIX,
                 updater.ASSET_EXE + updater.PART_SUFFIX):
        _remove(exe.with_name(name))
    _remove_stale_snapshots(time.time())


def _db_path() -> Path:
    return BASE_DIR / "hranilka.db"


def _snapshot_user_data() -> None:
    """Снимок данных на случай проблем после обновления. Ошибка прерывает
    установку: обновляться без страховки нельзя."""
    for src in (_db_path(), CONFIG_FILE):
        if not src.exists():
            continue
        dst = src.with_name(src.name + SNAPSHOT_SUFFIX)
        tmp = src.with_name(src.name + SNAPSHOT_SUFFIX + ".tmp")
        try:
            bk._copy_durable(src, tmp)
            os.replace(tmp, dst)
        finally:
            _remove(tmp)


def _replace(src: Path, dst: Path) -> None:
    """os.replace с повторами: антивирус ненадолго держит файлы открытыми."""
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_DELAY_SECS)


def _spawn(exe: Path, arg: str) -> subprocess.Popen[bytes]:
    """Запустить независимый экземпляр. Без PYINSTALLER_RESET_ENVIRONMENT
    onefile-загрузчик счёл бы его дочерним и взял временную папку старой
    версии, которая удаляется при её выходе. Выход из job-объекта — чтобы новый
    процесс не завершился вместе со старым; если job этого не разрешает,
    запускаем в нём."""
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)

    def start(creationflags: int) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [str(exe), arg], cwd=str(exe.parent), env=env, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=creationflags)

    try:
        return start(flags | breakaway)
    except PermissionError:
        return start(flags)


def _wait_confirmation(proc: subprocess.Popen[bytes], marker: Path,
                       timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.exists():
            return True
        if proc.poll() is not None:
            return marker.exists()
        time.sleep(_POLL_SECS)
    return marker.exists()


def _terminate_tree(proc: subprocess.Popen[bytes]) -> None:
    """Снять новую версию вместе с дочерним процессом onefile: proc — только
    загрузчик, а дочерний процесс продолжил бы держать exe и откат не прошёл бы."""
    taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                            "System32", "taskkill.exe")
    try:
        subprocess.run([taskkill, "/F", "/T", "/PID", str(proc.pid)],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        logger.error("Обновление: taskkill не сработал: %s", e)
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.error("Обновление: новая версия не завершилась после снятия")


class _RollbackFailed(Exception):
    """Прежний exe не удалось вернуть на место: он остался в .old."""


def _restore(old: Path, exe: Path) -> None:
    try:
        _replace(old, exe)
    except OSError as e:
        logger.critical("Обновление: прежняя версия осталась в %s: %s", old, e)
        raise _RollbackFailed() from e


def _swap_and_confirm(staged: StagedUpdate, exe: Path, timeout: float) -> bool:
    """Заменить exe и дождаться метки новой версии. False — exe возвращён."""
    old = exe.with_name(exe.name + OLD_SUFFIX)
    marker = exe.with_name(CONFIRM_MARKER)
    _remove(marker)
    _replace(exe, old)
    try:
        _replace(staged.path, exe)
    except OSError:
        _restore(old, exe)
        raise
    proc: Optional[subprocess.Popen[bytes]] = None
    try:
        proc = _spawn(exe, CONFIRM_ARG)
        if _wait_confirmation(proc, marker, timeout):
            _remove(marker)
            return True
        logger.error("Обновление: новая версия не подтвердила запуск (код %s)",
                     proc.poll())
    except OSError as e:
        logger.error("Обновление: новая версия не запустилась: %s", e)
    if proc is not None and proc.poll() is None:
        _terminate_tree(proc)
    _restore(old, exe)            # заменяет и сам неработающий новый файл
    return False


def install(staged: StagedUpdate, exe: Path,
            timeout: float = _CONFIRM_TIMEOUT_SECS) -> Optional[Path]:
    """Установить обновление. None — успех, иначе путь рабочей старой версии.
    Хэш сверяется повторно: файл мог измениться с момента проверки."""
    working = exe
    try:
        if updater.file_sha256(staged.path) != staged.sha256:
            raise UpdateError("файл обновления изменился после проверки")
        _snapshot_user_data()
        if _swap_and_confirm(staged, exe, timeout):
            logger.info("Обновление до %s установлено", staged.version)
            return None
    except _RollbackFailed:
        working = exe.with_name(exe.name + OLD_SUFFIX)
    except (OSError, UpdateError) as e:
        logger.error("Обновление не установлено: %s", e)
    updater.discard(staged.path)
    return working


def finish_pending() -> int:
    """Вызвать после выхода из цикла событий. Код выхода процесса."""
    global _pending
    staged, _pending = _pending, None
    if staged is None:
        return 0
    working = install(staged, updater.running_exe())
    if working is None:
        return 0
    try:
        _spawn(working, FAILED_ARG)
    except OSError as e:
        logger.critical("Обновление: не удалось перезапустить прежнюю версию "
                        "%s: %s", working, e)
        return 1
    return 0
