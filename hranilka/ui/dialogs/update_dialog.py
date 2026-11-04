"""Окно «Доступна новая версия»: описание релиза, ссылка для ручного обновления,
кнопка автоматического. Текст релиза — внешние данные, показывается как простой
текст: разметка и ссылки из него не интерпретируются."""
import html
import logging
import threading
from typing import Optional

from PySide6.QtCore import Signal, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
                               QPushButton)

from hranilka import __version__
from hranilka.services import updater
from hranilka.services.updater import (ReleaseInfo, StagedUpdate, UpdateCancelled,
                                       UpdateError)
from hranilka.ui.theme import ThemedDialog

logger = logging.getLogger(__name__)

_BYTES_IN_MB = 1024 * 1024
_NO_NOTES = "Описание изменений не указано."


class UpdateDialog(ThemedDialog):
    install_ready = Signal(object)      # StagedUpdate: файл скачан и проверен

    _progress = Signal(int, int)
    _done = Signal(object)
    _failed = Signal(str)

    def __init__(self, config, release: ReleaseInfo,
                 blocker: Optional[str] = None, parent=None):
        super().__init__(config, parent)
        self.release = release
        self._cancel = threading.Event()
        self._busy = False
        self._worker: Optional[threading.Thread] = None
        self.setWindowTitle("Доступна новая версия")
        self.setMinimumWidth(640)

        self.body.addWidget(QLabel(
            f"Доступна версия {release.version} (сейчас {__version__})."))
        self.body.addWidget(QLabel("Что нового:"))
        notes = QPlainTextEdit(release.notes.strip() or _NO_NOTES)
        notes.setReadOnly(True)
        notes.setMinimumHeight(220)
        self.body.addWidget(notes, 1)

        url = html.escape(release.page_url, quote=True)
        link = QLabel(f'Обновить вручную: <a href="{url}" '
                      f'style="color: {config.get("text_color", "#000000")};">'
                      f'страница релиза</a> — скачайте Hranilka.exe и замените им '
                      f'старый файл, данные рядом с ним останутся.')
        link.setWordWrap(True)
        link.setTextFormat(Qt.RichText)
        link.setOpenExternalLinks(False)
        link.linkActivated.connect(self._open_release_page)
        self.link_label = link
        self.body.addWidget(link)

        self.bar = QProgressBar()
        self.bar.setRange(0, max(1, release.exe_size // _BYTES_IN_MB))
        self.bar.setVisible(False)
        self.body.addWidget(self.bar)
        self.status = QLabel(blocker or (
            "Автообновление скачает файл, проверит подпись и перезапустит "
            "программу. База и настройки сохранятся."))
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        row = QHBoxLayout()
        row.addStretch()
        self.update_btn = QPushButton("Обновить автоматически")
        self.update_btn.setDefault(True)
        self.update_btn.setEnabled(blocker is None)
        self.update_btn.clicked.connect(self._start)
        self.close_btn = QPushButton("Позже")
        self.close_btn.clicked.connect(self.reject)
        row.addWidget(self.update_btn)
        row.addWidget(self.close_btn)
        self.body.addLayout(row)

        self._progress.connect(self._on_progress)
        self._done.connect(self._on_done)
        self._failed.connect(self._on_failed)

    def worker_alive(self) -> bool:
        """Поток загрузки ещё работает: после отмены он завершается не сразу."""
        return self._worker is not None and self._worker.is_alive()

    def _open_release_page(self, _href: str) -> None:
        # Адрес из release, а не из разметки: он уже проверен в parse_release.
        QDesktopServices.openUrl(QUrl(self.release.page_url))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.bar.setVisible(busy)
        self.update_btn.setEnabled(not busy)
        self.close_btn.setText("Отмена" if busy else "Позже")

    def _start(self) -> None:
        self._cancel.clear()
        self._set_busy(True)
        self.bar.setValue(0)
        self.status.setText("Загрузка обновления…")
        self._worker = threading.Thread(target=self._work, daemon=True,
                                        name="update-download")
        self._worker.start()

    def _work(self) -> None:
        """Фоновый поток: виджеты не трогаем, результат — только сигналами."""
        try:
            self._done.emit(updater.prepare(
                self.release, self._progress.emit, self._cancel.is_set))
        except UpdateCancelled:
            logger.info("Загрузка обновления отменена")
        except UpdateError as e:
            self._failed.emit(str(e))
        except Exception:                               # noqa: BLE001
            logger.exception("Сбой загрузки обновления")
            self._failed.emit("Непредвиденная ошибка. Подробности — в логе.")

    def _on_progress(self, done: int, total: int) -> None:
        self.bar.setValue(done // _BYTES_IN_MB)
        if done >= total:
            self.status.setText("Проверка подписи…")

    def _on_done(self, staged: StagedUpdate) -> None:
        if self._cancel.is_set():
            updater.discard(staged.path)
            return
        self._busy = False
        self.accept()
        self.install_ready.emit(staged)

    def _on_failed(self, message: str) -> None:
        if self._cancel.is_set():
            return
        self._set_busy(False)
        self.status.setText(f"Обновление не выполнено: {message}\n"
                            f"Воспользуйтесь ссылкой выше.")

    def reject(self) -> None:
        # Без подтверждения: окно закрывает и авто-блокировка по простою.
        self._cancel.set()
        super().reject()
