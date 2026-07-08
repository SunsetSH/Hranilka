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

from PySide6.QtCore import (Qt, QObject, QThread, QEventLoop, QTimer,
                            Signal, Slot)

from hranilka.ui import theme
from hranilka.data.database import VaultConflictError


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
        if self.db.encrypted:
            self.flush()
            if not self.wait_idle():
                return False, ("Фоновое сохранение базы не завершилось вовремя.\n"
                               "Повторите операцию через несколько секунд.")
        self._vault_locked = True
        try:
            result = fn()
        except Exception as e:                       # noqa: BLE001 — отдаём наверх
            return False, str(e)
        finally:
            self._vault_locked = False
        # Накопленные за время операции правки записать после снятия монополии.
        if self._write_pending or self.db._dirty:
            self.schedule_flush()
        return True, result

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
