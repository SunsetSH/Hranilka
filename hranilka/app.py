"""Bootstrap приложения (этап 6 реструктуризации): QApplication, иконка,
qasync-цикл asyncio+Qt, показ главного окна. Вынесен из main.py — корневой
main.py остался тонкой точкой входа (python main.py и PyInstaller)."""
import asyncio
import logging
import sys

import qasync
from PySide6.QtGui import QIcon, QImageReader
from PySide6.QtWidgets import QApplication

from hranilka.paths import RESOURCE_DIR
from hranilka.ui.main_window import MainWindow


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

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

    window = MainWindow()
    window.show()

    with event_loop:
        event_loop.run_until_complete(app_close_event.wait())
