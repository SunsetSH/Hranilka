"""Окно разблокировки мастер-паролем / recovery-кодом (вынесено из
dialogs.py, этап 4)."""
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QProgressBar, QPushButton)

from hranilka.crypto import store as cs
from hranilka.services import backup as bk
from hranilka.ui.theme import ThemedDialog, themed_confirm


def browse_backup_file(parent):
    """Выбрать файл бэкапа через проводник. Возвращает путь или None."""
    path, _ = QFileDialog.getOpenFileName(
        parent, "Выберите файл бэкапа", "",
        "База Хранилки (*.db);;Все файлы (*)")
    return path or None


def pick_backup(config, parent):
    """Предложить бэкапы из настроенной папки; если папка не задана или
    пуста — открыть проводник. Возвращает путь к файлу или None."""
    folder = config.get("backup_folder", "").strip()
    backups = bk.list_backups(folder) if folder else []
    if not backups:
        return browse_backup_file(parent)

    d = ThemedDialog(config, parent)
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
        p = browse_backup_file(d)
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


class UnlockDialog(ThemedDialog):
    """Окно ввода мастер-пароля при запуске / после автоблокировки.

    При успехе self.result_data = (db_bytes, dek, header). При отмене (выход)
    результат остаётся None — вызывающий код должен завершить программу.

    Разблокировка (Argon2id + расшифровка всего контейнера) — тяжёлый CPU,
    выполняется в фоновом потоке (H-09): результат приходит queued-сигналом
    _unlock_done, ввод на время заблокирован, поколение _gen отсекает
    устаревший результат после закрытия диалога."""

    _unlock_done = Signal(int, object, object)   # gen, result_data, exception

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
        self._busy = False            # фоновая разблокировка в работе
        self._gen = 0                 # поколение попытки (см. reject)
        self._unlock_done.connect(self._on_unlock_done)

        lay = self.body
        lay.addWidget(QLabel("Введите мастер-пароль для доступа к базе:"))
        field_row = QHBoxLayout()
        self._field = QLineEdit()
        self._field.setEchoMode(QLineEdit.Password)
        field_row.addWidget(self._field, 1)
        self._paste_btn = QPushButton("Вставить")
        self._paste_btn.setToolTip("Вставить из буфера обмена")
        self._paste_btn.clicked.connect(self._paste)
        field_row.addWidget(self._paste_btn)
        lay.addLayout(field_row)

        self._rec_check = QCheckBox("Использовать recovery-код вместо пароля")
        self._rec_check.toggled.connect(self._on_mode_toggle)
        lay.addWidget(self._rec_check)

        self._err = QLabel("")
        self._err.setWordWrap(True)
        lay.addWidget(self._err)

        # Indeterminate-прогресс на время KDF/расшифровки (H-09).
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        lay.addWidget(self._progress)

        self._forgot_btn = QPushButton("Забыли пароль и recovery-код?")
        self._forgot_btn.clicked.connect(self._forgot)
        forgot_row = QHBoxLayout()
        forgot_row.addWidget(self._forgot_btn)
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

    def _pick_backup(self, parent):
        return pick_backup(self.config, parent)

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
        if self._busy:
            return
        secret = self._field.text().strip()
        if not secret:
            return
        # Байты контейнера нужны именно здесь (не при показе диалога): дожидаемся
        # фонового чтения файла (M7-06). При ошибке чтения — сообщение и выход.
        if not self._ensure_container():
            return
        is_rec = self._rec_check.isChecked()
        # Argon2id + расшифровка контейнера — тяжёлый CPU: в фоновый поток (H-09).
        # Кнопка «Выход» остаётся доступной; поздний результат отсекается по _gen.
        self._set_busy(True)
        gen, container, done = self._gen, self._container, self._unlock_done

        def work():
            try:
                done.emit(gen, cs.unlock(container, secret, is_recovery=is_rec), None)
            except Exception as e:       # noqa: BLE001 — классифицируется в GUI-потоке
                done.emit(gen, None, e)

        threading.Thread(target=work, daemon=True, name="unlock-kdf").start()

    def _set_busy(self, busy: bool):
        self._busy = busy
        for w in (self._field, self._ok, self._rec_check,
                  self._paste_btn, self._forgot_btn):
            w.setEnabled(not busy)
        self._progress.setVisible(busy)
        self._err.setText("Расшифровка…" if busy else "")

    def _on_unlock_done(self, gen: int, result, error):
        if gen != self._gen:
            return                       # устаревший результат: диалог закрыт/перезапущен
        self._set_busy(False)
        if error is None:
            self.result_data = result
            self.accept()
        elif isinstance(error, cs.WrongPassword):
            self._err.setText("Неверный пароль или recovery-код.")
            self._field.selectAll()
            self._field.setFocus()
        elif isinstance(error, cs.CorruptVault):
            self._err.setText(f"Файл базы повреждён: {error}")
        else:
            self._err.setText(f"Ошибка разблокировки: {error}")

    def reject(self):
        # «Выход» во время фоновой разблокировки: результат, который придёт
        # позже, игнорируется по несовпадению поколения.
        self._gen += 1
        super().reject()

