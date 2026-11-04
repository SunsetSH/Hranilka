"""Bootstrap приложения (этап 6 реструктуризации): QApplication, иконка,
qasync-цикл asyncio+Qt, показ главного окна. Вынесен из main.py — корневой
main.py остался тонкой точкой входа (python main.py и PyInstaller)."""
import asyncio
import logging
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler

import qasync
from PySide6.QtCore import QMetaObject, Qt
from PySide6.QtGui import QIcon, QImageReader
from PySide6.QtWidgets import QApplication, QMessageBox

from hranilka.core.paths import BASE_DIR, RESOURCE_DIR
from hranilka.services import update_install
from hranilka.ui.main_window import MainWindow
from hranilka.ui.retro_style import RetroStyle


LOG_FILE_NAME = "hranilka.log"
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_handling_error = False


def setup_logging(log_dir=None) -> None:
    """Консоль + ротируемый файл (3 x 512 КБ) в BASE_DIR. Секреты в лог не
    пишутся: только сообщения и трейсбеки."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(_LOG_FORMAT)
    if not any(isinstance(h, logging.StreamHandler)
               and not isinstance(h, RotatingFileHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    path = (log_dir or BASE_DIR) / LOG_FILE_NAME
    try:
        fh = RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=3,
                                 encoding="utf-8")
    except OSError as e:
        logging.warning("Лог-файл недоступен (%s): %s", path, e)
        return
    fh.setFormatter(fmt)
    root.addHandler(fh)


def _report_uncaught(exc_type, exc, tb) -> None:
    """Залогировать необработанное исключение и показать диалог."""
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        return
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    logging.critical("Необработанное исключение:\n%s", text)
    app = QApplication.instance()
    if _handling_error or app is None:
        return
    message = (f"{exc_type.__name__}: {exc}\n\nПодробности записаны в "
               f"{LOG_FILE_NAME} (рядом с программой).")
    if threading.current_thread() is not threading.main_thread():
        # Виджеты нельзя создавать вне GUI-потока: показываем диалог в нём.
        QMetaObject.invokeMethod(
            app, lambda: _show_error(message), Qt.ConnectionType.QueuedConnection)
        return
    _show_error(message)


def _show_error(message: str) -> None:
    global _handling_error
    if _handling_error:
        return
    _handling_error = True
    try:
        QMessageBox.critical(None, "Хранилка: непредвиденная ошибка", message)
    except Exception:                                   # noqa: BLE001
        logging.exception("Не удалось показать диалог ошибки")
    finally:
        _handling_error = False


def install_excepthooks() -> None:
    sys.excepthook = _report_uncaught
    threading.excepthook = lambda a: _report_uncaught(
        a.exc_type, a.exc_value, a.exc_traceback)


def main() -> int:
    """Запуск приложения. Возвращает код выхода процесса."""
    setup_logging()
    install_excepthooks()
    try:
        return _run()
    except SystemExit as e:           # sys.exit(0) из MainWindow.__init__
        code = e.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    except Exception:                                   # noqa: BLE001
        _report_uncaught(*sys.exc_info())
        return 1


def _run() -> int:
    app = QApplication(sys.argv)
    app.setStyle(RetroStyle("Fusion"))

    app_icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256):
        app_icon.addFile(str(RESOURCE_DIR / "assets" / f"icon_{size}.png"))
    app.setWindowIcon(app_icon)

    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Hranilka.App")
    # Backstop против «decompression bomb» при декодировании (M3-05): ограничиваем
    # объём памяти на одно изображение. 256 МБ вмещают допустимые ~50 Мп (RGBA
    # ≈200 МБ), но отсекают аномально большие картинки из БД/бэкапа.
    QImageReader.setAllocationLimit(256)

    # Гибридный событийный цикл asyncio+Qt (qasync): asyncio-операции (async-доступ
    # к БД через db.run_async, фоновая загрузка картинок) выполняются в том же
    # цикле, что и Qt, поэтому UI остаётся отзывчивым. Цикл крутится до aboutToQuit
    # (закрытие последнего окна / app.quit()).
    event_loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(event_loop)
    app_close_event = asyncio.Event()
    app.aboutToQuit.connect(app_close_event.set)

    update_install.confirm_startup(sys.argv)
    window = MainWindow()
    window.show()
    window.register_session_notifications()

    with event_loop:
        event_loop.run_until_complete(app_close_event.wait())
    return update_install.finish_pending()
