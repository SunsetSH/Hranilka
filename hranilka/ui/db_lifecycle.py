"""Жизненный цикл файла БД главного окна (DbLifecycleMixin): открытие и
разблокировка, восстановление из бэкапа, откат, стирание всех данных."""
import logging
from pathlib import Path

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

from hranilka.core import diagnostics
from hranilka.services import backup as bk
from hranilka.data.database import FutureSchemaError, PreMigrationBackupError
from hranilka.core import util
from hranilka.ui import theme


class StartupAborted(SystemExit):
    """Запуск прерван (выход из разблокировки, занятая БД, несовместимая схема).
    Подкласс SystemExit: прежняя семантика sys.exit сохранена, но причина и код
    явные; MainWindow.create() превращает его в None."""

    def __init__(self, code: int = 0, reason: str = ""):
        super().__init__(code)
        self.reason = reason


class DbLifecycleMixin:
    def _archive_db_file(self, suffix: str = "locked"):
        """Отложить (переименовать) текущий файл БД, не удаляя его.

        suffix: "locked" — недоступный зашифрованный файл (утерян секрет),
        "corrupt" — повреждённый файл SQLite (H-07).

        Возвращает путь к отложенному файлу или None, если файла не было.
        OSError переименования пробрасывается: вызыватель ОБЯЗАН отменить
        замену файла — иначе «сохранённый для диагностики» оригинал был бы
        молча перезаписан."""
        import os
        from datetime import datetime
        if not os.path.exists(self.db.db_path):
            return None
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        archived = f"{self.db.db_path}.{suffix}-{ts}"
        os.replace(self.db.db_path, archived)
        logging.info("Файл БД отложен: %s", archived)
        return archived

    def _create_tables_or_exit(self):
        """create_tables() с понятным отказом, если база создана более новой
        версией программы (схема новее поддерживаемой). «Миграция вниз»
        повредила бы данные, поэтому корректнее завершить работу.

        Повреждение страниц/заголовка SQLite всплывает здесь при первом реальном
        чтении схемы (H-07): fail-closed — соединение закрывается без записи,
        пользователю предлагается восстановление из проверенного бэкапа."""
        import sqlite3
        while True:
            try:
                self.db.create_tables()
                diagnostics.startup_stage("схема БД проверена")
                return
            except FutureSchemaError as e:
                logging.error("Несовместимая версия схемы БД: %s", e)
                theme.themed_info(
                    self.config, self, "Несовместимая версия базы", str(e),
                )
            except PreMigrationBackupError as e:
                # Не удалось создать резервную копию перед необратимой правкой
                # схемы (H7-03): база НЕ тронута. Открытие прерываем с понятным
                # сообщением, чтобы пользователь освободил место и повторил.
                logging.error("Отказ открытия: %s", e)
                theme.themed_info(
                    self.config, self, "Не удалось подготовить базу", str(e),
                )
            except sqlite3.DatabaseError as e:
                self.db.close(persist=False)
                if self._handle_db_open_error(e) and self._open_database():
                    continue
            self.vault.shutdown()
            self._instance_lock.release()
            raise StartupAborted(1, "БД не открыта/не создана")

    def _handle_db_open_error(self, err, parent=None) -> bool:
        """Классифицировать ошибку открытия БД: не всякий sqlite3.DatabaseError —
        порча. Временная блокировка внешним процессом (SQLITE_BUSY/LOCKED) не
        должна вести в recovery с заменой здоровой базы бэкапом.

        Возвращает True, если открытие стоит повторить (пользователь просит
        повтор / бэкап восстановлен), False — выход без изменений файла."""
        import sqlite3
        code = getattr(err, "sqlite_errorcode", None)
        primary = code & 0xFF if code is not None else None
        if primary in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            logging.warning("Файл БД временно заблокирован: %s", err)
            return theme.themed_confirm(
                self.config, parent, "База занята",
                "Файл базы временно заблокирован другой программой\n"
                "(SQLite-инструмент, антивирус, резервное копирование).\n\n"
                "Файл не повреждён и не изменён. Повторить попытку открытия?")
        is_corruption = (primary in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)
                         # Нет кода (нестандартная сборка): DatabaseError вне
                         # OperationalError трактуем как порчу структуры.
                         or (code is None
                             and not isinstance(err, sqlite3.OperationalError)))
        if is_corruption:
            return self._offer_corrupt_recovery(err, parent)
        # I/O, права доступа и прочее: файл может быть цел — recovery с заменой
        # не предлагаем, ничего не пишем.
        logging.error("Не удалось открыть файл БД: %s", err)
        theme.themed_info(
            self.config, parent, "Ошибка открытия базы",
            f"Не удалось открыть файл базы:\n{err}\n\n"
            "Файл не изменён. Проверьте диск/права доступа и запустите снова.")
        return False

    def _fail_closed_archive_stranded(self, archived: str, parent=None):
        """Аварийный fail-closed: восстановление сорвалось, И вернуть отложенный
        файл на место не удалось. Продолжать цикл открытия нельзя — connect()
        молча создал бы пустую базу, и пользователь решил бы, что данные
        пропали. Показываем путь к данным; вызыватель обязан завершить запуск."""
        logging.critical("Данные остались в отложенном файле: %s", archived)
        theme.themed_info(
            self.config, parent, "Восстановление прервано",
            "Не удалось восстановить бэкап И вернуть исходный файл на место.\n\n"
            f"Ваши данные сохранены в файле:\n{archived}\n\n"
            "Программа закроется, чтобы не создать пустую базу поверх.\n"
            "Переименуйте этот файл обратно в hranilka.db вручную\n"
            "и запустите программу снова.")

    def _offer_corrupt_recovery(self, err, parent=None) -> bool:
        """Fail-closed обработка повреждённого файла БД (H-07).

        В подозрительный файл ничего не пишется; он откладывается
        (*.corrupt-<ts>) только после явного согласия пользователя на
        восстановление. Возвращает True, если бэкап восстановлен и открытие
        можно повторить, False — пользователь выбрал выход."""
        from hranilka.ui.dialogs.unlock import pick_backup
        from hranilka.ui.theme import ThemedDialog, themed_confirm
        logging.error("Файл БД повреждён: %s", err)

        d = ThemedDialog(self.config, parent)
        d.setWindowTitle("База повреждена")
        d.setMinimumWidth(480)
        lay = d.body
        lay.addWidget(QLabel(
            "Файл базы данных повреждён и не может быть открыт:\n"
            f"{err}\n\n"
            "В повреждённый файл ничего не записано. При восстановлении из\n"
            "бэкапа он будет сохранён рядом (переименован) для диагностики.\n\n"
            "Выберите, как продолжить:"))
        restore_btn = QPushButton("Восстановить из бэкапа…")
        exit_btn = QPushButton("Выход")
        lay.addWidget(restore_btn)
        rr = QHBoxLayout(); rr.addStretch(); rr.addWidget(exit_btn)
        lay.addLayout(rr)

        restored = {"ok": False}

        def do_restore():
            import os
            path = pick_backup(self.config, d)
            if not path:
                return
            # Кандидат проверяется ДО любых изменений текущего файла: невалидный
            # бэкап не должен стоить нам переименованного оригинала.
            if not bk.validate_backup(path):
                theme.themed_info(
                    self.config, d, "Ошибка",
                    "Выбранный файл повреждён или не является базой Хранилки.\n"
                    "Текущие файлы не тронуты.")
                return
            if not themed_confirm(
                    self.config, d, "Восстановление из бэкапа",
                    "Повреждённый файл будет отложен (переименован),\n"
                    "на его место встанет выбранный бэкап. Продолжить?"):
                return
            # Архивирование обязано удаться: обещали сохранить оригинал для
            # диагностики — при сбое восстановление отменяется, файлы не тронуты.
            try:
                archived = self._archive_db_file("corrupt")
            except OSError as e:
                logging.error("Не удалось отложить повреждённый файл: %s", e)
                theme.themed_info(
                    self.config, d, "Ошибка",
                    f"Не удалось отложить повреждённый файл:\n{e}\n"
                    "Восстановление отменено, файлы не тронуты.")
                return
            try:
                # restore_backup повторно проверяет кандидата и имеет свой
                # rollback на время замены (H3-02).
                bk.restore_backup(path, self.db.db_path)
            except Exception as e:                     # noqa: BLE001
                logging.error("Восстановление бэкапа не удалось: %s", e)
                # Вернуть отложенный оригинал: иначе рабочего файла нет и
                # следующий запуск молча создал бы пустую базу.
                if archived is not None:
                    try:
                        os.replace(archived, self.db.db_path)
                    except OSError as e2:
                        logging.error("Не удалось вернуть отложенный файл: %s", e2)
                        # Файл остался под архивным именем — аварийный выход:
                        # restored["ok"]=False, вызыватели завершают запуск.
                        self._fail_closed_archive_stranded(archived, d)
                        d.reject()
                        return
                theme.themed_info(self.config, d, "Ошибка",
                                  f"Не удалось восстановить бэкап:\n{e}")
                return
            restored["ok"] = True
            d.accept()

        restore_btn.clicked.connect(do_restore)
        exit_btn.clicked.connect(d.reject)
        d.exec()
        return restored["ok"]

    @staticmethod
    def _read_container_async(path):
        """Запустить чтение файла-контейнера в отдельном потоке и вернуть Future
        (H-9). В __init__ event-loop qasync ещё не крутится, поэтому используем
        ThreadPoolExecutor, а не loop.run_in_executor. Пул закрывается сам после
        завершения задачи (shutdown(wait=False))."""
        import concurrent.futures

        def _read():
            with open(path, "rb") as f:
                return f.read()

        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(_read)
        ex.shutdown(wait=False)                  # не ждём здесь — Future отдаёт результат
        return fut

    def _open_database(self, parent=None):
        """Открыть СОЕДИНЕНИЕ с БД: зашифрованную — через ввод мастер-пароля/
        recovery-кода, обычную — напрямую. При утере секрета пользователь может
        восстановить бэкап или начать с чистой базы. Возвращает True при успехе,
        False если пользователь выбрал «Выход». Источник истины о шифровании —
        сигнатура файла.

        ВАЖНО: только открывает соединение и НЕ валидирует/не мигрирует схему —
        это делает отдельный шаг (_validate_schema_or_exit при старте/lock либо
        _open_and_validate_after_restore с откатом при restore). Так один и тот же
        опенер годится и для сценариев, где ошибку схемы нужно откатить, а не
        завершать программу (H65-05)."""
        import sqlite3
        from hranilka.crypto import store as cs
        from hranilka.ui.dialogs import UnlockDialog
        while True:
            if not cs.is_encrypted_file(self.db.db_path):
                try:
                    self.db.connect()
                except sqlite3.DatabaseError as e:
                    # Fail-closed (H-07): ничего не писать. Классификация
                    # отличает порчу (recovery) от временной блокировки
                    # внешним процессом (повтор) и I/O-ошибок (выход).
                    self.db.close(persist=False)
                    if self._handle_db_open_error(e, parent):
                        continue
                    return False
                self.config.set("encryption_enabled", False)
                self.config.save()
                diagnostics.startup_stage("открыта обычная БД")
                return True

            # Чтение зашифрованного контейнера (может быть крупным) выносим в
            # фоновый поток и запускаем ДО построения диалога (H-9/M7-06). Future
            # передаём в сам диалог: он показывается МОМЕНТАЛЬНО, а тяжёлое чтение
            # файла перекрывается вводом пароля. Байты берутся лениво, лишь при
            # первой попытке разблокировки (там же всплывёт OSError чтения). На
            # повторной итерации цикла создаётся свежий future.
            reader = self._read_container_async(self.db.db_path)
            dlg = UnlockDialog(self.config, b"", parent)
            dlg.set_container_future(reader)
            diagnostics.pause_watchdog()     # ввод пароля — не зависание
            logging.info("Запуск: показано окно разблокировки")
            dlg.exec()

            if dlg.result_data is not None:
                diagnostics.startup_stage("секрет принят, открытие контейнера")
                self.db.open_encrypted(*dlg.result_data)
                diagnostics.startup_stage("контейнер открыт, сохранение настроек")
                self.config.set("encryption_enabled", True)
                self.config.save()
                diagnostics.startup_stage("открыта зашифрованная БД")
                return True

            logging.info("Окно разблокировки закрыто без пароля: %s",
                         dlg.recovery_action or "выход")

            if dlg.recovery_action == "reset":
                try:
                    self._archive_db_file()
                except OSError as e:
                    # Файл не отложен — новую базу поверх не создаём (обещали
                    # сохранить зашифрованный оригинал). Снова окно разблокировки.
                    theme.themed_info(self.config, parent, "Ошибка",
                                      f"Не удалось отложить файл БД:\n{e}")
                # Файла нет → connect() создаст новую пустую базу.
                continue

            if dlg.recovery_action == "restore" and dlg.restore_path:
                # Кандидат проверяется ДО архивирования: невалидный бэкап не
                # должен стоить нам переименованного оригинала.
                if not bk.validate_backup(dlg.restore_path):
                    theme.themed_info(
                        self.config, parent, "Ошибка",
                        "Выбранный файл повреждён или не является базой "
                        "Хранилки.\nТекущие файлы не тронуты.")
                    continue
                try:
                    archived = self._archive_db_file()
                except OSError as e:
                    theme.themed_info(self.config, parent, "Ошибка",
                                      f"Не удалось отложить файл БД:\n{e}\n"
                                      "Восстановление отменено.")
                    continue
                # Через restore_backup (а не прямой copy2): кандидат проверяется
                # повторно, замена атомарна, есть откат на время подмены (H3-02).
                try:
                    bk.restore_backup(dlg.restore_path, self.db.db_path)
                except Exception as e:
                    # Вернуть отложенный оригинал: иначе рабочего файла нет и
                    # следующая итерация молча создала бы пустую базу.
                    if archived is not None:
                        import os
                        try:
                            os.replace(archived, self.db.db_path)
                        except OSError as e2:
                            logging.error("Не удалось вернуть отложенный файл: %s",
                                          e2)
                            # Аварийный fail-closed: цикл продолжать нельзя.
                            self._fail_closed_archive_stranded(archived, parent)
                            return False
                    theme.themed_info(self.config, parent, "Ошибка",
                                      f"Не удалось восстановить бэкап:\n{e}")
                # Повторяем цикл: восстановленный файл может быть как обычным,
                # так и зашифрованным (другим паролем) — тогда снова спросим.
                continue

            return False  # «Выход»

    def _open_and_validate_after_restore(self):
        """Открыть переоткрытую БД И провалидировать/мигрировать её схему (H65-05).

        Plaintext и encrypted проходят один путь: после открытия соединения
        выполняется create_tables() (миграции, обязательные таблицы, отказ при
        схеме новее поддерживаемой — FutureSchemaError). Возвращает True только
        если БД открыта и схема валидна; при любой ошибке закрывает БД без записи
        и возвращает False — вызыватель откатывается к прежнему vault. Так
        восстановленный encrypted-файл больше не публикуется для чтения/записи без
        миграции и проверки версии схемы."""
        # Окно ввода пароля и сообщения ниже модальны: idle-таймер не должен
        # закрыть их блокировкой посреди restore (тихий откат восстановления).
        # Тот же флаг, что у повторной разблокировки в _lock_vault.
        was_unlocking = self._unlocking
        self._unlocking = True
        try:
            return self._open_and_validate_guarded()
        finally:
            self._unlocking = was_unlocking

    def _open_and_validate_guarded(self):
        if not self._open_database(self):
            return False
        try:
            self.db.create_tables()
        except FutureSchemaError as e:
            logging.error("Восстановленный файл: несовместимая схема: %s", e)
            theme.themed_info(self.config, self, "Несовместимая версия базы", str(e))
            self.db.close(persist=False)
            return False
        except Exception as e:                       # noqa: BLE001
            logging.error("Восстановленный файл не прошёл проверку схемы: %s",
                          e, exc_info=e)
            theme.themed_info(self.config, self, "Ошибка базы",
                              f"Восстановленный файл не прошёл проверку схемы:\n{e}")
            self.db.close(persist=False)
            return False
        return True

    def _restore_from_backup(self, path):
        """Восстановление из бэкапа (вызывается из «Настроек»). Возвращает
        (ok, err) для показа в диалоге.

        Критично для plaintext-режима (Баг 1, WinError 5): SQLite держит файл
        hranilka.db открытым, и `os.replace` внутри restore_backup на Windows
        падает. Поэтому соединение закрываем ДО замены файла. Закрываем БЕЗ
        сохранения — иначе текущая БД затёрла бы восстановленный файл. После
        замены переоткрываем БД (восстановленный файл может оказаться
        зашифрованным — тогда _open_database спросит пароль)."""
        import os
        # H7-01: перед закрытием БД гарантируем, что последние изменения попали в
        # ФАЙЛ (иначе restore незаметно потерял бы их в шифр. режиме). Порядок важен:
        #   1) барьер executor'а — дожидаемся всех in-flight run_async-мутаторов; они,
        #      закоммитив, помечают БД грязной (иначе коммит мог бы случиться уже
        #      ПОСЛЕ wait_idle и остался бы несохранённым);
        #   2) синхронный flush — запускает запись, если БД грязная (в т.ч. когда
        #      сброс был лишь запланирован через QTimer, но ещё не стартовал);
        #   3) wait_idle — дожидаемся завершения самой фоновой записи.
        # _quiesce_guarded: автоблокировка не должна сработать в его event-loop.
        if not self.vault._quiesce_guarded():
            return False, ("Фоновые операции с базой не завершились вовремя.\n"
                           "Повторите попытку через несколько секунд.")
        # Гасим незавершённый async карточки/галереи ДО закрытия БД (H65-02).
        self._quiesce_card_async()
        self.db.close(persist=False)
        db_path = self.db.db_path
        # Страховочная копия текущей БД (durable: fsync содержимого). Восстановленный
        # encrypted-файл проверяется лишь структурно (_is_valid_db), а подлинность
        # (GCM), пароль и версия схемы — только при открытии. Если открыть/проверить
        # не удалось, вернём прежнюю БД из этой копии.
        rollback = None
        if os.path.exists(db_path):
            rollback = db_path + ".pre-restore"
            # Durable-копия большой базы (fsync) — в фоне с progress: окно не
            # получает Not Responding. Гейт сохранён (run_exclusive_busy).
            ok, err = self.vault.run_exclusive_busy(
                lambda: bk._copy_durable(Path(db_path), Path(rollback)),
                "Создание страховочной копии текущей базы…")
            if not ok:
                # H65-03: рабочий файл есть, но страховочную копию создать не удалось.
                # Продолжать restore нельзя — при неоткрытии кандидата рабочая БД
                # пропала бы безвозвратно. Отменяем restore и возвращаем прежнюю БД.
                logging.error("Не удалось создать страховочную копию перед restore: %s", err)
                self._discard_file(rollback)
                if self._open_and_validate_after_restore():
                    self._after_db_reopened(None)
                    return False, ("Не удалось создать страховочную копию; "
                                   f"восстановление отменено:\n{err}")
                self.close()
                return False, f"Не удалось создать страховочную копию: {err}"
        # Копирование кандидата, fsync и quick_check — тоже в фоне (гейт тот же).
        ok, err = self.vault.run_exclusive_busy(
            lambda: bk.restore_backup(path, db_path),
            "Восстановление из бэкапа: копирование и проверка…")
        if not ok:
            # restore_backup при сбое откатывает файл к прежнему состоянию —
            # переоткрываем БД как была и сообщаем об ошибке.
            self._discard_file(rollback)
            if not self._open_and_validate_after_restore():
                self.close()
                return False, err
            self._after_db_reopened(None)
            return False, err
        if not self._open_and_validate_after_restore():
            # Восстановленный файл не открылся/не аутентифицирован/не прошёл
            # проверку схемы. Возвращаем прежнюю БД из страховочной копии.
            if self._rollback_restore(rollback, db_path):
                return False, ("Восстановленный файл не удалось открыть или проверить; "
                               "возвращена прежняя база.")
            self.close()
            return False, "Восстановленный файл не удалось открыть или проверить."
        self._discard_file(rollback)
        self._after_db_reopened("База данных восстановлена из бэкапа.")
        return True, None

    def _discard_file(self, path):
        """Тихо удалить временную страховочную копию БД (.pre-restore), если она
        есть. Это ПОЛНАЯ копия базы (все пароли/BLOB в открытом виде), поэтому
        затираем содержимое нулями перед удалением, а не просто unlink (H-3)."""
        import os
        if path and os.path.exists(path):
            util.best_effort_wipe(path)

    def _rollback_restore(self, rollback, db_path):
        """Вернуть прежнюю БД из страховочной копии после неудачного восстановления.
        Возвращает True, если прежняя база возвращена, открыта и провалидирована."""
        import os
        if not rollback or not os.path.exists(rollback):
            return False
        try:
            os.replace(rollback, db_path)
        except OSError as e:
            logging.warning("Не удалось вернуть прежнюю БД: %s", e)
            return False
        if not self._open_and_validate_after_restore():
            return False
        self._after_db_reopened("Восстановление отменено: возвращена прежняя база.")
        return True

    def _after_db_reopened(self, message):
        """Сброс состояния и UI после переоткрытия БД (восстановление бэкапа)."""
        self._current_account_id = None
        self._current_fin = None
        self._current_server = None
        self._edit_cache.clear()
        self._dirty_ids.clear()
        self.current_account_data = None
        self.is_editing = False
        self._any_db_changes = False
        self._show_placeholder()
        self._reload_tree()
        self._update_bin_button()
        if message:
            self.statusBar().showMessage(message, 3000)

    # ─── Удаление всех данных ────────────────────────────────────────────────

    def _wipe_all_data(self):
        # Останавливаем операции карточек/галерей и инвалидируем текущую
        # сессию ДО барьера executor-а. Уже queued run_async не сможет записать
        # данные после очистки; выполняющаяся операция завершится до барьера.
        self._quiesce_card_async()
        self.db.invalidate_async_session()
        if not self.db.wait_executor_idle():
            theme.themed_info(
                self.config, self, "Операции не завершены",
                "Фоновые операции с базой ещё идут. Полная очистка отменена; "
                "подождите несколько секунд и повторите попытку.",
            )
            return
        try:
            self.db.wipe_all_data()
        except Exception as e:                   # noqa: BLE001
            logging.error("Не удалось удалить все данные: %s", e, exc_info=e)
            theme.themed_info(
                self.config, self, "Ошибка удаления",
                "Не удалось удалить все данные. Подробности — в логе программы.",
            )
            return
        # Также удаляем все бэкапы в выбранной папке.
        deleted = 0
        folder = self.config.get("backup_folder", "").strip()
        if folder:
            try:
                deleted = bk.delete_all_backups(folder)
            except Exception as e:
                logging.warning("Не удалось удалить бэкапы: %s", e)
        self._current_account_id = None
        self._current_fin = None
        self._current_server = None
        self._edit_cache.clear()
        self._dirty_ids.clear()
        self.current_account_data = None
        self.is_editing = False
        self._any_db_changes = False
        self._show_placeholder()
        self._reload_tree()
        self._update_bin_button()
        msg = "Все данные удалены."
        if deleted:
            msg += f" Удалено бэкапов: {deleted}."
        self.statusBar().showMessage(msg, 4000)
