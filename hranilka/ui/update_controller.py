"""UpdateController: проверка обновлений в фоне и запуск установки из главного
окна. Сеть — в отдельном потоке (services/updater), результат приходит сигналом."""
import logging
import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from hranilka.services import update_install, updater
from hranilka.services.updater import ReleaseInfo, StagedUpdate, UpdateError
from hranilka.ui import theme
from hranilka.ui.dialogs.update_dialog import UpdateDialog

logger = logging.getLogger(__name__)

_MODAL_RETRY_MS = 3000
_REMOVE_OLD_EXE_DELAY_MS = 5000     # старая версия как раз завершается
_STATUS_MESSAGE_MS = 8000


class UpdateController(QObject):
    _checked = Signal(object, object)          # release, текст ошибки

    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window
        self._checking = False
        self._manual: Optional[tuple] = None     # (parent, on_done) ручной проверки
        self._dialog: Optional[UpdateDialog] = None
        self._checked.connect(self._on_checked)

    def on_startup(self) -> None:
        """Вызвать после показа окна: итог обновления, уборка, фоновая проверка."""
        update_install.cleanup_leftovers()
        QTimer.singleShot(_REMOVE_OLD_EXE_DELAY_MS, update_install.remove_old_exe)
        self._report_startup_state()
        if self._window.config.get("check_updates", False):
            self._start_check()

    def _report_startup_state(self) -> None:
        state = update_install.take_startup_state()
        if state == "updated":
            self._window.statusBar().showMessage(
                "Программа обновлена.", _STATUS_MESSAGE_MS)
        elif state == "failed":
            theme.themed_info(
                self._window.config, self._window, "Обновление не выполнено",
                "Обновление не установлено, программа осталась на прежней "
                "версии. Данные не затронуты.\nПодробности — в hranilka.log.",
                icon="warning")

    def check_now(self, parent, on_done: Callable[[], None]) -> None:
        """Ручная проверка: результат показывается всегда, on_done — по концу."""
        self._manual = (parent, on_done)
        self._start_check()

    def _start_check(self) -> None:
        """Идущая проверка подхватит ручной запрос своим результатом."""
        if self._checking:
            return
        self._checking = True
        threading.Thread(target=self._work, daemon=True,
                         name="update-check").start()

    def _work(self) -> None:
        """Фоновый поток: только сеть и сигнал."""
        release, error = None, None
        try:
            release = updater.fetch_latest()
        except UpdateError as e:
            error = str(e)
        except Exception:                               # noqa: BLE001
            logger.exception("Сбой проверки обновлений")
            error = "Непредвиденная ошибка. Подробности — в логе."
        self._checked.emit(release, error)

    def _on_checked(self, release: Optional[ReleaseInfo],
                    error: Optional[str]) -> None:
        self._checking = False
        request, self._manual = self._manual, None
        if request is None:
            if error is not None:           # при запуске сбой сети не мешает работе
                logger.info("Проверка обновлений при запуске: %s", error)
            elif release is not None:
                self._present(release, self._window)
            return
        parent, on_done = request
        on_done()
        parent = parent if parent.isVisible() else self._window
        if error is not None:
            theme.themed_info(self._window.config, parent,
                              "Ошибка проверки обновлений", error)
        elif release is None:
            theme.themed_info(self._window.config, parent, "Обновления",
                              "Установлена последняя версия.")
        else:
            self._present(release, parent)

    def _present(self, release: ReleaseInfo, parent) -> None:
        """Показать окно релиза. Фоновый результат ждёт, пока пользователь
        закончит с другим модальным окном (разблокировка, настройки). Отменённая
        загрузка должна завершиться: новая пишет в те же файлы."""
        if not self._window.isVisible():
            return                          # программа закрывается
        modal = QApplication.activeModalWidget()
        previous_alive = self._dialog is not None and self._dialog.worker_alive()
        if previous_alive or (modal is not None and modal is not parent):
            QTimer.singleShot(_MODAL_RETRY_MS, lambda: self._present(release, parent))
            return
        self._dialog = UpdateDialog(self._window.config, release,
                                    updater.auto_update_blocker(), parent)
        self._dialog.install_ready.connect(self._begin_install)
        self._dialog.open()

    def _begin_install(self, staged: StagedUpdate) -> None:
        """Обычное закрытие программы, затем update_install.finish_pending. Шаги —
        через очередь событий: модальное окно должно успеть закрыться."""
        update_install.request_install(staged)
        QTimer.singleShot(0, self._close_modal)

    def _close_modal(self) -> None:
        modal = QApplication.activeModalWidget()
        if modal is not None:
            modal.reject()
        QTimer.singleShot(0, self._close_window)

    def _close_window(self) -> None:
        """Отказ («Вернуться» при несохранённых правках) отменяет установку."""
        if QApplication.activeModalWidget() is None and self._window.close():
            QApplication.quit()
            return
        update_install.cancel_install()
        theme.themed_info(self._window.config, self._window, "Обновление отменено",
                          "Программа не закрыта, обновление отменено.",
                          icon="info")
