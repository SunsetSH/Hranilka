"""Окно разблокировки мастер-паролем / recovery-кодом (вынесено из
dialogs.py, этап 4)."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton)

from hranilka.crypto import store as cs
from hranilka.services import backup as bk
from hranilka.ui.theme import ThemedDialog, themed_confirm


class UnlockDialog(ThemedDialog):
    """Окно ввода мастер-пароля при запуске / после автоблокировки.

    При успехе self.result_data = (db_bytes, dek, header). При отмене (выход)
    результат остаётся None — вызывающий код должен завершить программу."""

    def __init__(self, config, container: bytes = b"", parent=None):
        super().__init__(config, parent)
        self.setWindowTitle("Разблокировка")
        self.setModal(True)
        self.setMinimumWidth(420)
        # Байты контейнера могут прийти сразу (готовые) ИЛИ отложенно через
        # set_container_future (M7-06): тяжёлое чтение крупного файла тогда идёт
        # параллельно вводу пароля, а диалог показывается моментально. _container
        # хранит уже прочитанные байты; _container_future — ещё не завершённое
        # чтение (résolve при первой попытке разблокировки).
        self._container = container
        self._container_future = None
        self.result_data = None
        self.recovery_action = None   # None | "reset" | "restore"
        self.restore_path = None

        lay = self.body
        lay.addWidget(QLabel("Введите мастер-пароль для доступа к базе:"))
        field_row = QHBoxLayout()
        self._field = QLineEdit()
        self._field.setEchoMode(QLineEdit.Password)
        field_row.addWidget(self._field, 1)
        paste_btn = QPushButton("Вставить")
        paste_btn.setToolTip("Вставить из буфера обмена")
        paste_btn.clicked.connect(self._paste)
        field_row.addWidget(paste_btn)
        lay.addLayout(field_row)

        self._rec_check = QCheckBox("Использовать recovery-код вместо пароля")
        self._rec_check.toggled.connect(self._on_mode_toggle)
        lay.addWidget(self._rec_check)

        self._err = QLabel("")
        self._err.setWordWrap(True)
        lay.addWidget(self._err)

        forgot_btn = QPushButton("Забыли пароль и recovery-код?")
        forgot_btn.clicked.connect(self._forgot)
        forgot_row = QHBoxLayout()
        forgot_row.addWidget(forgot_btn)
        forgot_row.addStretch()
        lay.addLayout(forgot_row)

        row = QHBoxLayout()
        row.addStretch()
        self._ok = QPushButton("Разблокировать")
        self._ok.setDefault(True)
        self._ok.clicked.connect(self._attempt)
        exit_btn = QPushButton("Выход")
        exit_btn.clicked.connect(self.reject)
        row.addWidget(self._ok)
        row.addWidget(exit_btn)
        lay.addLayout(row)

        self._field.returnPressed.connect(self._attempt)
        QTimer.singleShot(0, self._field.setFocus)

    def set_container_future(self, future):
        """Отдать диалогу ЕЩЁ НЕ завершённое чтение файла-контейнера (M7-06).

        Диалог показывается сразу, а тяжёлое чтение крупного файла идёт в фоне и
        перекрывается вводом пароля. Байты берутся лениво — только когда они реально
        нужны (первая попытка разблокировки, см. _ensure_container). OSError из
        future всплывёт там же и будет показан пользователю."""
        self._container_future = future

    def _ensure_container(self) -> bool:
        """Гарантировать наличие байт контейнера (résolve future при необходимости).

        Возвращает True, если байты готовы (в self._container), False — если чтение
        файла завершилось ошибкой (сообщение уже показано в поле ошибки диалога).
        На время ожидания показываем «Чтение файла…» и блокируем кнопку — future
        обычно уже завершён (чтение шло параллельно вводу пароля)."""
        fut = self._container_future
        if fut is None:
            return True
        self._err.setText("Чтение файла…")
        self._ok.setEnabled(False)
        QApplication.processEvents()   # дать метке «Чтение файла…» отрисоваться
        try:
            self._container = fut.result()
            self._container_future = None
            self._err.setText("")
            return True
        except OSError as e:
            self._err.setText(f"Не удалось прочитать файл базы: {e}")
            return False
        finally:
            self._ok.setEnabled(True)

    def _paste(self):
        self._field.setText(QApplication.clipboard().text().strip())
        self._err.setText("")

    def _forgot(self):
        """Меню действий, если утеряны и пароль, и recovery-код. Расшифровать
        текущие данные невозможно — можно восстановить бэкап или начать заново."""
        d = ThemedDialog(self.config, self)
        d.setWindowTitle("Доступ к данным утерян")
        d.setMinimumWidth(460)
        lay = d.body
        lay.addWidget(QLabel(
            "Без мастер-пароля или recovery-кода расшифровать текущие данные\n"
            "НЕВОЗМОЖНО. Текущий зашифрованный файл будет сохранён рядом\n"
            "(переименован) на случай, если код вспомнится.\n\n"
            "Выберите, как продолжить:"))
        restore_btn = QPushButton("Восстановить из бэкапа…")
        reset_btn = QPushButton("Начать с чистой базы")
        cancel_btn = QPushButton("Отмена")
        lay.addWidget(restore_btn)
        lay.addWidget(reset_btn)
        rr = QHBoxLayout(); rr.addStretch(); rr.addWidget(cancel_btn)
        lay.addLayout(rr)

        def do_restore():
            path = self._pick_backup(d)
            if not path:
                return
            if not themed_confirm(
                self.config, d, "Восстановление из бэкапа",
                "Текущие зашифрованные данные станут недоступны "
                "(файл будет отложен). Восстановить выбранный бэкап?"):
                return
            self.recovery_action = "restore"
            self.restore_path = path
            d.accept()
            self.accept()

        def do_reset():
            if not themed_confirm(
                self.config, d, "Начать с чистой базы",
                "Будет создана новая пустая база. Доступ к текущим "
                "зашифрованным данным будет утерян. Продолжить?"):
                return
            self.recovery_action = "reset"
            d.accept()
            self.accept()

        restore_btn.clicked.connect(do_restore)
        reset_btn.clicked.connect(do_reset)
        cancel_btn.clicked.connect(d.reject)
        d.exec()

    @staticmethod
    def _browse_backup_file(parent):
        path, _ = QFileDialog.getOpenFileName(
            parent, "Выберите файл бэкапа", "",
            "База Хранилки (*.db);;Все файлы (*)")
        return path or None

    def _pick_backup(self, parent):
        """Предложить бэкапы из настроенной папки; если папка не задана или
        пуста — открыть проводник. Возвращает путь к файлу или None."""
        folder = self.config.get("backup_folder", "").strip()
        backups = bk.list_backups(folder) if folder else []
        if not backups:
            return self._browse_backup_file(parent)

        d = ThemedDialog(self.config, parent)
        d.setWindowTitle("Выбор бэкапа")
        d.setMinimumSize(440, 320)
        lay = d.body
        lay.addWidget(QLabel(f"Бэкапы из папки:\n{folder}"))
        lst = QListWidget()
        for p in backups:
            item = QListWidgetItem(p.name)
            item.setData(Qt.UserRole, str(p))
            lst.addItem(item)
        lst.setCurrentRow(0)
        lst.itemDoubleClicked.connect(lambda *_: d.accept())
        lay.addWidget(lst, 1)

        chosen = {"path": None}
        row = QHBoxLayout()
        browse_btn = QPushButton("Другой файл…")
        row.addWidget(browse_btn)
        row.addStretch()
        ok = QPushButton("Выбрать"); ok.setDefault(True); ok.clicked.connect(d.accept)
        cancel = QPushButton("Отмена"); cancel.clicked.connect(d.reject)
        row.addWidget(ok); row.addWidget(cancel)
        lay.addLayout(row)

        def do_browse():
            p = self._browse_backup_file(d)
            if p:
                chosen["path"] = p
                d.accept()
        browse_btn.clicked.connect(do_browse)

        if d.exec() != QDialog.Accepted:
            return None
        if chosen["path"]:
            return chosen["path"]
        item = lst.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _on_mode_toggle(self, use_recovery: bool):
        if use_recovery:
            self._field.setEchoMode(QLineEdit.Normal)
            self._field.setPlaceholderText("XXXX-XXXX-XXXX-…")
        else:
            self._field.setEchoMode(QLineEdit.Password)
            self._field.setPlaceholderText("")
        self._field.clear()
        self._err.setText("")

    def _attempt(self):
        secret = self._field.text().strip()
        if not secret:
            return
        # Байты контейнера нужны именно здесь (не при показе диалога): дожидаемся
        # фонового чтения файла (M7-06). При ошибке чтения — сообщение и выход.
        if not self._ensure_container():
            return
        is_rec = self._rec_check.isChecked()
        try:
            self.result_data = cs.unlock(self._container, secret, is_recovery=is_rec)
            self.accept()
        except cs.WrongPassword:
            self._err.setText("Неверный пароль или recovery-код.")
            self._field.selectAll()
            self._field.setFocus()
        except cs.CorruptVault as e:
            self._err.setText(f"Файл базы повреждён: {e}")

