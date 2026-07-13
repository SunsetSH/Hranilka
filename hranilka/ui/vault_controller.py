"""Контроллер файла-хранилища: фоновая запись зашифрованного контейнера,
отложенный flush и монопольный гейт привилегированных операций.

Вынесен из MainWindow (Эпик 4): здесь сосредоточена вся работа с ФАЙЛОМ БД и
сериализацией сохранения, тогда как окно отвечает только за UI. За UI-побочки
(строка статуса, модальные вопросы о конфликте) контроллер обращается к окну —
оно же служит родителем диалогов.

Критично (H3-01): запись сериализована. Фоновый воркер пишет контейнер не
конкурируя ни с другой фоновой записью (коалесценция _write_busy/_write_pending),
ни с синхронными привилегированными операциями (гейт run_exclusive + флаг
_vault_locked, под которым отложенный flush воркеру не сабмитится)."""
import logging
import threading

from PySide6.QtCore import (Qt, QMetaObject, QObject, QThread, QEventLoop,
                            QTimer, Signal, Slot)
from PySide6.QtWidgets import QLabel, QProgressBar, QWidget

from hranilka.ui import theme
from hranilka.data.database import VaultConflictError


class _BusyDialog(theme.ThemedDialog):
    """Модальный «Выполняется…» для run_exclusive_busy: indeterminate progress,
    пользователь закрыть НЕ может (Esc/заголовок игнорируются) — диалог
    закрывает только завершение фоновой операции."""

    def __init__(self, config, parent, message: str):
        super().__init__(config, parent)
        self.setWindowTitle("Подождите")
        self.setModal(True)
        self.setMinimumWidth(380)
        lbl = QLabel(message)
        lbl.setWordWrap(True)
        bar = QProgressBar()
        bar.setRange(0, 0)               # indeterminate: KDF не даёт прогресса
        self.body.addWidget(lbl)
        self.body.addWidget(bar)

    def reject(self):
        pass


class _VaultWriter(QObject):
    """Фоновая запись зашифрованного контейнера на диск.

    Живёт в отдельном потоке. Получает ГОТОВЫЙ снимок БД (db_bytes), сделанный
    в GUI-потоке (там, где живёт соединение SQLite), и выполняет самое тяжёлое —
    шифрование AES-GCM и атомарную запись на диск — не блокируя интерфейс.
    Не обращается к соединению SQLite, поэтому потокобезопасен относительно него."""

    done = Signal(bool, str, bool)   # ok, текст_ошибки, признак_конфликта
    _job = Signal(bool)              # внутренний: force → в свой поток

    def __init__(self, db):
        super().__init__()
        self._db = db
        # Очередь из одного задания: сигнал доставляется в поток воркера.
        self._job.connect(self._do, Qt.ConnectionType.QueuedConnection)

    def submit(self, force=False):
        self._job.emit(force)

    @Slot(bool)
    def _do(self, force):
        ok, err, conflict = True, "", False
        try:
            # Снимок БД (serialize) делаем здесь, в потоке писателя: соединение
            # потокобезопасно (RLock + check_same_thread=False), поэтому UI-поток
            # больше не тратит сотни мс на копирование большой базы. Затем —
            # шифрование AES-GCM и атомарная запись (самое тяжёлое), тоже вне UI.
            db_bytes = self._db.serialize_db()
            self._db.seal_and_write(db_bytes, force=force)
        except VaultConflictError:
            ok, conflict, err = False, True, "conflict"
        except Exception as e:                       # noqa: BLE001 — отдаём наверх
            ok, err = False, str(e)
        self.done.emit(ok, err, conflict)


class VaultController(QObject):
    """Владеет фоновым writer'ом, отложенным flush и гейтом монопольного доступа.

    window — главное окно (родитель модальных диалогов, источник statusBar)."""

    # Реле «БД стала грязной» → schedule_flush на UI-потоке. Мутаторы БД теперь
    # могут выполняться в фоновом потоке-исполнителе (db.run_async); их callback
    # _on_dirty нельзя звать напрямую, т.к. schedule_flush использует
    # QTimer.singleShot, требующий UI-потока. Сигнал с AutoConnection доставляется
    # в поток получателя (UI): эмит из воркера — очередью, из UI-потока — напрямую.
    _dirty_relay = Signal()

    def __init__(self, db, window, config):
        super().__init__()
        self.db = db
        self._window = window
        self.config = config

        self._write_busy = False        # воркер сейчас пишет
        self._write_pending = False     # во время записи появились новые правки
        self._writer_idle_loop = None   # локальный event-loop ожидания (close/lock)
        self._db_flush_scheduled = False
        # Монопольный доступ к файлу-БД для привилегированных операций: пока флаг
        # взведён, отложенный flush НЕ сабмитит воркеру, лишь копит _write_pending.
        self._vault_locked = False

        # БД помечает себя «грязной» → планируем сброс один раз за оборот цикла.
        # Через реле-сигнал, чтобы пометка из фонового потока БД попадала на UI.
        self._dirty_relay.connect(self.schedule_flush)
        self.db._on_dirty = self._dirty_relay.emit

        self._writer = _VaultWriter(self.db)
        self._writer_thread = QThread(self)
        self._writer.moveToThread(self._writer_thread)
        self._writer.done.connect(self._on_vault_written)
        self._writer_thread.start()

    def _status(self, text, timeout=0):
        self._window.statusBar().showMessage(text, timeout)

    # ─── Отложенная запись БД на диск (шифр. режим) ──────────────────────────

    def schedule_flush(self):
        """Запланировать сброс БД на диск к концу оборота событийного цикла.
        Серия операций (например, цикл по мультивыбору) схлопывается в одну
        запись."""
        if self._vault_locked:
            # Привилегированная операция держит файл монопольно — не планируем
            # фоновую запись, лишь помечаем, что снимок нужен после неё.
            self._write_pending = True
            return
        if not self._db_flush_scheduled:
            self._db_flush_scheduled = True
            QTimer.singleShot(0, self.flush)

    def flush(self):
        self._db_flush_scheduled = False
        if self._vault_locked:
            # Между планированием и срабатыванием singleShot началась
            # привилегированная операция — отложить запись до её завершения.
            self._write_pending = True
            return
        if not self.db.encrypted:
            # Обычный режим: данные уже в файле, flush — дешёвый no-op.
            try:
                self.db.flush()
            except Exception as e:
                logging.warning("Не удалось сохранить БД на диск: %s", e)
            return
        if not self.db._dirty:
            return
        if self._write_busy:
            # Воркер занят — запишем свежий снимок сразу после текущей записи.
            self._write_pending = True
            return
        self._start_vault_write()

    def _start_vault_write(self, force=False):
        """Запустить фоновое сохранение. Снимок БД (serialize), шифрование и запись
        выполняет поток писателя — UI-поток лишь помечает занятость и ставит задачу,
        поэтому даже большая база (сотни МБ) больше не подвешивает интерфейс."""
        # Оптимистично считаем изменения «в работе»: новые правки снова поставят
        # _dirty и запланируют следующий flush. При сбое вернём _dirty=True.
        self.db._dirty = False
        self._write_busy = True
        self._write_pending = False
        self._status("Сохранение…")
        self._writer.submit(force)

    def _on_vault_written(self, ok, err, conflict):
        """Завершение фоновой записи (в GUI-потоке)."""
        self._write_busy = False
        if ok:
            self._status("Сохранено.", 1500)
            # Появились правки во время записи — пишем свежий снимок.
            if self._write_pending or self.db._dirty:
                self._start_vault_write()
        else:
            # Запись не удалась — данные снова считаем несохранёнными.
            self.db._dirty = True
            if conflict:
                if theme.themed_confirm(
                    self.config, self._window, "Файл изменён извне",
                    "Файл базы изменён другой программой с момента открытия.\n"
                    "Перезаписать его своими данными?",
                ):
                    self._start_vault_write(force=True)
                else:
                    self._status("Сохранение отменено: файл изменён извне.", 5000)
            else:
                logging.warning("Не удалось сохранить БД на диск: %s", err)
                self._status("ОШИБКА СОХРАНЕНИЯ!", 5000)
                theme.themed_info(
                    self.config, self._window, "Ошибка сохранения",
                    f"Не удалось сохранить базу на диск:\n{err}\n\n"
                    "Изменения остаются в памяти. Освободите место/проверьте "
                    "доступ к файлу и повторите.",
                )
        # Разбудить ожидающий close/lock, если воркер освободился.
        if self._writer_idle_loop is not None and not self._write_busy:
            self._writer_idle_loop.quit()

    def wait_idle(self, timeout_ms=15000) -> bool:
        """Дождаться завершения текущей фоновой записи (для close/lock/гейта).
        Крутит локальный event-loop, поэтому done доставляется и UI не виснет.

        Возвращает True, если воркер свободен, и False, если за timeout_ms запись
        не завершилась (M3-01). При False вызыватель ОБЯЗАН прервать свой шаг: не
        запускать вторую запись, не закрывать БД, не обнулять ключ, не снимать
        instance-lock — иначе возможна потеря/порча данных при живом воркере."""
        if not self._write_busy:
            return True
        loop = QEventLoop()
        self._writer_idle_loop = loop
        QTimer.singleShot(timeout_ms, loop.quit)   # страховочный таймаут
        loop.exec()
        self._writer_idle_loop = None
        # _on_vault_written сбрасывает _write_busy перед quit() — по нему и
        # отличаем штатное завершение от срабатывания страховочного таймаута.
        return not self._write_busy

    def run_exclusive(self, fn):
        """Выполнить привилегированную операцию над файлом-БД МОНОПОЛЬНО (H3-01).

        Привилегированные операции (вкл/выкл шифрования, смена пароля,
        regenerate recovery, бэкап/восстановление) пишут/читают файл-БД
        синхронно. Если в этот момент фоновый воркер тоже пишет — возможна
        потеря/откат/порча данных. Гейт:
          1) в шифр. режиме сбрасывает отложенный снимок и СТРОГО дожидается
             воркера; при таймауте — отказ (op не выполняется);
          2) на время op взводит _vault_locked, чтобы singleShot(0)-флаши из
             event-loop модального диалога не запускали параллельную запись;
          3) выполняет fn() синхронно;
          4) снимает флаг и, если за время op появились правки, планирует flush.

        Возвращает (ok: bool, result_or_error): при успехе — то, что вернул fn()
        (например, recovery-код); при ошибке — текст для показа пользователю."""
        if not self._quiesce():
            return False, ("Фоновые операции с базой не завершились вовремя.\n"
                           "Повторите операцию через несколько секунд.")
        self._vault_locked = True
        try:
            result = fn()
        except Exception as e:                       # noqa: BLE001 — отдаём наверх
            return False, str(e)
        finally:
            self._vault_locked = False
            self._note_window_activity()
        # Накопленные за время операции правки записать после снятия монополии.
        if self._write_pending or self.db._dirty:
            self.schedule_flush()
        return True, result

    def _quiesce(self) -> bool:
        """Дочистить фоновую активность перед привилегированной операцией.
        Возвращает False при таймауте — вызыватель обязан отказаться от операции.

        Порядок тот же, что при restore (H7-01), и он ПРИНЦИПИАЛЕН:
          1) барьер run_async — in-flight мутаторы (сохранение карточки)
             дорабатывают в старой сессии и помечают БД грязной;
          2) flush — записывает СВЕЖИЙ снимок, включающий эти мутации;
          3) wait_idle — дожидаемся завершения самой фоновой записи.
        Обратный порядок (flush до барьера) дал бы на диске старый снимок:
        ручной бэкап скопировал бы файл без только что сохранённой карточки."""
        if not self.db.wait_executor_idle():
            return False
        if self.db.encrypted:
            self.flush()
            if not self.wait_idle():
                return False
        return True

    def _note_window_activity(self):
        """Сбросить счётчик простоя окна после привилегированной операции:
        шифрование большой базы может длиться дольше idle-интервала, и без
        сброса первый же тик заблокировал бы vault поверх диалога с одноразовым
        recovery-кодом."""
        note = getattr(self._window, "note_activity", None)
        if callable(note):
            note()

    def run_exclusive_busy(self, fn, message: str = "Выполняется операция…"):
        """run_exclusive для KDF-тяжёлых операций (H-09): fn выполняется в
        фоновом потоке, а GUI-поток крутит модальный progress-диалог — окно
        отзывчиво (не «Not Responding»), ввод заблокирован, повторные клики
        исключены. Гейт и сигнатура (ok, result_or_error) — как у run_exclusive.

        Потокобезопасность fn: соединение открыто с check_same_thread=False, а
        публичные методы Database сериализованы RLock'ом, поэтому вызов
        enable_encryption/change_master_password и т.п. из фонового потока
        корректен (см. database.py, авто-обёртка в лок)."""
        if not self._quiesce():
            return False, ("Фоновые операции с базой не завершились вовремя.\n"
                           "Повторите операцию через несколько секунд.")
        self._vault_locked = True
        # Родитель — только настоящий QWidget (в тестах окно может быть фейком).
        parent = self._window if isinstance(self._window, QWidget) else None
        dlg = _BusyDialog(self.config, parent, message)
        out = {}

        def work():
            try:
                out["res"] = (True, fn())
            except Exception as e:                   # noqa: BLE001 — отдаём наверх
                out["res"] = (False, str(e))
            # Закрыть диалог из GUI-потока; если exec ещё не начался, queued-событие
            # будет обработано первым же оборотом его event-loop.
            QMetaObject.invokeMethod(dlg, "accept", Qt.ConnectionType.QueuedConnection)

        thread = threading.Thread(target=work, daemon=True, name="vault-exclusive")
        try:
            thread.start()
            dlg.exec()                               # ждём accept от work()
            thread.join()                            # к этому моменту work() завершён
        finally:
            self._vault_locked = False
            dlg.deleteLater()
            # Долгая операция (KDF на большой базе) не должна засчитываться как
            # простой: без сброса первый тик idle-таймера заблокировал бы vault
            # поверх диалога с одноразовым recovery-кодом.
            self._note_window_activity()
        if self._write_pending or self.db._dirty:
            self.schedule_flush()
        return out["res"]

    def shutdown(self):
        """Корректно остановить поток фоновой записи (идемпотентно).

        Завершается на финальных путях выхода. Если фоновая запись не успела
        закончиться — лишь предупреждаем в лог: запись атомарна (уникальный temp
        + os.replace), поэтому прерывание файл не повредит, а thread.wait даёт
        текущему seal_and_write доработать."""
        thread = getattr(self, "_writer_thread", None)
        if thread is not None and thread.isRunning():
            if not self.wait_idle():
                logging.warning("Фоновая запись не завершилась к остановке потока.")
            thread.quit()
            thread.wait(3000)
