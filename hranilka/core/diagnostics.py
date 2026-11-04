"""Диагностика сбоев и зависаний: этапы запуска в логе, стеки потоков при
зависании запуска и native-сбоях.

faulthandler пишет напрямую в файл, минуя logging: при сбое интерпретатора
обычный лог уже недоступен. В файл попадают только имена файлов и функций,
значения переменных и секреты — нет."""
import faulthandler
import logging
import sys
import time
from pathlib import Path
from typing import IO, Any, Optional

CRASH_LOG_NAME = "hranilka-crash.log"
STARTUP_HANG_SECS = 30

_fault_file: Optional[IO[str]] = None
_started_at = time.monotonic()


def enable_fault_log(log_dir: Path) -> None:
    """Писать стеки всех потоков при native-сбое в файл рядом с программой.
    В сборке без консоли sys.stderr нет, поэтому файл обязателен."""
    global _fault_file
    if _fault_file is not None:
        return
    try:
        _fault_file = open(log_dir / CRASH_LOG_NAME, "a", encoding="utf-8")
    except OSError as e:
        logging.warning("Журнал сбоев недоступен: %s", e)
        return
    faulthandler.enable(file=_fault_file, all_threads=True)


def startup_stage(stage: str) -> None:
    """Этап запуска в лог + перевзвод сторожа: если следующий этап не наступит
    за STARTUP_HANG_SECS, стеки всех потоков попадут в журнал сбоев."""
    logging.info("Запуск [%.2f с]: %s", time.monotonic() - _started_at, stage)
    if _fault_file is not None:
        faulthandler.dump_traceback_later(STARTUP_HANG_SECS, file=_fault_file)


def pause_watchdog() -> None:
    """Снять сторож на время, зависящее от пользователя (ввод пароля)."""
    faulthandler.cancel_dump_traceback_later()


def startup_finished() -> None:
    logging.info("Запуск завершён за %.2f с", time.monotonic() - _started_at)
    pause_watchdog()


def _on_unraisable(args: Any) -> None:
    logging.error("Необрабатываемое исключение в %r: %s",
                  args.object, args.exc_value, exc_info=(
                      args.exc_type, args.exc_value, args.exc_traceback))


def log_loop_exception(_loop: Any, context: dict) -> None:
    """Обработчик asyncio-цикла: ошибки фоновых задач вместо потери в stderr."""
    logging.error("Ошибка asyncio: %s", context.get("message"),
                  exc_info=context.get("exception"))


def install_hooks() -> None:
    sys.unraisablehook = _on_unraisable
