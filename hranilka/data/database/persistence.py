"""Персистентность БД: обычный файл SQLite или AES-GCM-контейнер (шифрованный
режим — БД в :memory:), атомарная запись, ревизии файла на диске, отложенный
flush, включение/выключение шифрования. Часть класса Database (database.py) —
методы вынесены дословно, этап 2 реструктуризации.

ВНИМАНИЕ: конвейер отложенного сохранения (см. docs/CODE_MAP.md) пересекает
границу этого модуля и vault_controller.py — семантику методов не менять."""
import hashlib
import logging
import os
import sqlite3
import tempfile

from hranilka.core.util import best_effort_wipe
from hranilka.crypto import store as cs
from hranilka.data.errors import StaleSessionError, VaultConflictError
from hranilka.data.database.state import DbBase


class DbPersistenceMixin(DbBase):
    def _setup_conn(self):
        """Общие настройки соединения (row_factory, внешние ключи)."""
        self.conn.row_factory = sqlite3.Row  # Чтобы обращаться к полям по имени
        # Без этого ON DELETE CASCADE/SET NULL не работают в SQLite.
        self.conn.execute("PRAGMA foreign_keys = ON")
        # Затирать содержимое удаляемых страниц нулями, а не просто помечать их
        # свободными: иначе удалённые пароли/BLOB остаются читаемыми в файле БД
        # до следующего VACUUM (Баг 2). В in-memory (шифр.) режиме безвреден.
        self.conn.execute("PRAGMA secure_delete = ON")
        if not self.encrypted:
            # Явные значения (а не умолчания сборки SQLite) для файловой БД:
            # откатный журнал и полный fsync при коммите.
            self.conn.execute("PRAGMA journal_mode = DELETE")
            self.conn.execute("PRAGMA synchronous = FULL")
        self.cursor = self.conn.cursor()

    def connect(self) -> None:
        """Открыть обычную (незашифрованную) базу — файл на диске."""
        self.encrypted = False
        self._dek = None
        self._header = None
        self._dirty = False
        self._disk_fingerprint = None
        # check_same_thread=False: доступ из UI-потока и из воркера run_async
        # сериализуется RLock'ом (см. __init__), поэтому безопасно.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._disk_revision = self._stat_revision()
        self._setup_conn()
        self._bump_session()
        self._wipe_stale_update_holds()

    def open_encrypted(self, db_bytes: bytes, dek: bytes, header: dict) -> None:
        """Открыть расшифрованные байты БД в памяти. Файл остаётся шифрованным;
        изменения сбрасываются на диск через persist()."""
        self.encrypted = True
        # bytearray: ключ можно занулить in-place при lock() (S8).
        self._dek = bytearray(dek)
        self._header = header
        self._dirty = False
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        if db_bytes:
            self.conn.deserialize(db_bytes)  # type: ignore[attr-defined]  # есть в CPython 3.11+
        self._disk_revision = self._stat_revision()
        self._disk_fingerprint = self._file_fingerprint()
        self._setup_conn()
        self._bump_session()
        self._wipe_stale_update_holds()

    def _wipe_stale_update_holds(self) -> None:
        """Убрать `<db>.pre-update-wipe-*` — жёсткие ссылки на прежний plaintext,
        оставшиеся после сбоя между os.link и затиранием (_atomic_write_wiping_old).
        Best-effort. Не трогаем ссылку этого процесса, пока идёт замена (флаг
        _wipe_hold_active). Если ссылка указывает на тот же inode, что и живой
        файл БД (сбой ДО замены), её только удаляем: затирание уничтожило бы БД."""
        directory = os.path.dirname(self.db_path) or "."
        prefix = os.path.basename(self.db_path) + ".pre-update-wipe-"
        own = f"{prefix}{os.getpid()}"
        try:
            names = os.listdir(directory)
        except OSError:
            return
        for name in names:
            if not name.startswith(prefix):
                continue
            if name == own and getattr(self, "_wipe_hold_active", False):
                continue
            path = os.path.join(directory, name)
            try:
                if not os.path.isfile(path):
                    continue
                if os.path.exists(self.db_path) and os.path.samefile(path, self.db_path):
                    os.unlink(path)
                else:
                    best_effort_wipe(path)
                logging.info("Удалён остаток обновления: %s", name)
            except OSError as e:
                logging.warning("Не удалось убрать остаток %s: %s", name, e)

    def _file_fingerprint(self):
        """Отпечаток «поколения» контейнера: SHA-256 первых 512 байт файла
        (заголовок + случайный на каждое сохранение data_nonce + начало
        шифртекста). Только шифр. режим; None - файла нет/нечитаем/обычный
        режим. Дёшево, формат контейнера не меняет."""
        if not self.encrypted:
            return None
        try:
            with open(self.db_path, "rb") as f:
                return hashlib.sha256(f.read(512)).digest()
        except OSError:
            return None

    def _key_material(self):
        """Снимок (DEK, header) для шифрования - под _key_lock (B13).

        Возвращает НЕизменяемую копию DEK: lock() зануляет исходный bytearray,
        и шифрование, начатое до lock(), не должно получить обнулённый ключ.
        Ключа нет (БД уже заблокирована) - StaleSessionError, а не TypeError
        из глубины шифрования."""
        with self._key_lock:
            if self._dek is None or self._header is None:
                raise StaleSessionError()
            return bytes(self._dek), self._header

    @staticmethod
    def _zero(buf) -> None:
        """Занулить bytearray-ключ in-place (S8); не-bytearray игнорируется."""
        if isinstance(buf, bytearray):
            buf[:] = bytes(len(buf))

    def _stat_revision(self):
        """«Ревизия» файла на диске (st_mtime_ns, размер) или None, если файла нет."""
        try:
            st = os.stat(self.db_path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _check_no_external_change(self):
        """VaultConflictError, если состояние файла на диске разошлось с тем,
        что мы видели при открытии/последней записи — включая переходы
        «существует ↔ отсутствует» (M3-03):

          * файла не было, а теперь появился чужой — перезапись затёрла бы его;
          * файл был, а теперь удалён извне — нельзя молча создавать заново;
          * содержимое (mtime/размер) изменилось — кто-то писал параллельно."""
        current = self._stat_revision()
        if self._disk_revision is None:
            # На момент открытия файла не существовало. Любой появившийся файл —
            # внешний; молча перезаписать его нельзя.
            if current is not None:
                raise VaultConflictError()
            return
        # Файл существовал. Его удаление или изменение извне — конфликт.
        if current != self._disk_revision:
            # mtime/размер меняют антивирус/OneDrive без правки содержимого.
            # В шифр. режиме сверяем отпечаток: тот же контейнер - не конфликт.
            if (current is not None and self._disk_fingerprint is not None
                    and self._file_fingerprint() == self._disk_fingerprint):
                self._disk_revision = current
                return
            raise VaultConflictError()

    def _atomic_replace(self, data):
        """Атомарно заменить файл-БД содержимым data через УНИКАЛЬНЫЙ временный
        файл в том же каталоге (tempfile.mkstemp + fsync + os.replace).

        Уникальное имя temp на каждую запись принципиально (H3-01): при общем
        фиксированном `db_path + ".tmp"` две одновременные записи (фоновый
        воркер + синхронная привилегированная операция) затирали бы временный
        файл друг друга, давая порчу/потерю данных. mkstemp гарантирует, что у
        каждой записи свой temp. Ревизию диска обновляем после успешной замены.

        data - bytes или последовательность частей (пишутся подряд, без склейки
        в одну копию - P1)."""
        parts = (data,) if isinstance(data, (bytes, bytearray, memoryview)) else data
        directory = os.path.dirname(self.db_path) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".hranilka-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                for part in parts:
                    f.write(part)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.db_path)
        except BaseException:
            # Не оставлять временный файл при любом сбое записи/замены.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._disk_revision = self._stat_revision()
        self._disk_fingerprint = self._file_fingerprint()

    def _write_container(self, container, force: bool = False):
        """Атомарно записать готовый контейнер (bytes или части) на диск
        (temp+fsync+replace).
        Перед заменой сверяет ревизию файла (если не force) и обновляет её после.

        Проверка ревизии, замена файла и обновление _disk_revision/fingerprint -
        под _write_lock: иначе два параллельных писателя видят одну и ту же
        «старую» ревизию и один получает ложный VaultConflictError."""
        with self._write_lock:
            if not force:
                self._check_no_external_change()
            self._atomic_replace(container)

    def serialize_db(self) -> bytes:
        """Сериализовать in-memory БД в байты.

        ОБЯЗАНО выполняться в потоке-владельце соединения SQLite (соединение
        однопоточное). Это относительно дешёвая операция (копирование в память);
        тяжёлые шифрование и запись на диск вынесены в seal_and_write()."""
        return self.conn.serialize()

    def serialize_container(self) -> bytes:
        """serialize_db() + шифрование в контейнер (без записи). Синхронный путь."""
        dek, header = self._key_material()
        return cs.seal(self.serialize_db(), dek, header)

    def seal_and_write(self, db_bytes: bytes, force: bool = False):
        """Зашифровать ГОТОВЫЕ байты БД и атомарно записать контейнер.

        Не обращается к соединению SQLite — безопасно вызывать из рабочего
        потока (см. фоновую запись в main.py). Шифрование (AES-GCM) и запись —
        самые тяжёлые части сохранения."""
        # Ключ и заголовок снимаются один раз в начале (B13): параллельный
        # lock() не обнулит их посреди шифрования.
        # Снимок ключа/заголовка берём ВНУТРИ _write_lock: писатель, ждавший
        # лок, иначе записал бы устаревший заголовок поверх нового (смена
        # пароля/recovery между снимком и записью). Шифрование идёт под
        # _write_lock, но НЕ под основным _lock - чтения БД не блокируются.
        # Порядок локов везде один: _lock -> _write_lock (обратного нет).
        with self._write_lock:
            dek, header = self._key_material()
            parts = cs.seal_parts(db_bytes, dek, header)
            self._write_container(parts, force=force)

    def persist(self, force: bool = False):
        """Сбросить текущее состояние БД на диск.

        В обычном режиме — no-op (SQLite уже пишет в файл). В зашифрованном —
        сериализует in-memory БД, шифрует и атомарно записывает контейнер.
        VaultConflictError, если файл изменён извне (force=True — перезаписать)."""
        if not self.encrypted or self.conn is None or self._dek is None:
            return
        self.seal_and_write(self.serialize_db(), force=force)

    def set_header(self, header: dict):
        """Обновить заголовок контейнера (после смены пароля/recovery) и сохранить.

        Транзакционно (H5-02): новый заголовок применяется к self._header только
        ПОСЛЕ успешной записи на диск. Если persist() упадёт (нет места, конфликт
        файла), откатываем self._header к прежнему и пробрасываем исключение —
        иначе UI показал бы успех, а на диске остался бы старый контейнер,
        рассогласованный с заголовком в памяти."""
        old_header = self._header
        self._header = header
        try:
            self.persist()
        except BaseException:
            self._header = old_header
            raise

    def lock(self, force: bool = False):
        """Заблокировать: сохранить, закрыть in-memory БД, забыть ключ.

        Если сохранение не удалось (нет места, отказ ACL, блокировка
        антивирусом), исключение пробрасывается наверх, а БД и ключ НЕ
        обнуляются — несохранённые данные остаются доступны для повторной
        попытки. Раньше ошибка глоталась, и последние изменения терялись тихо.
        force — перезаписать файл, изменённый извне (см. VaultConflictError)."""
        if self.encrypted:
            self.persist(force=force)   # при сбое — исключение, состояние сохранится
            self._dirty = False
        if self.conn:
            self.conn.close()
        self.conn = None
        self.cursor = None
        with self._key_lock:
            # Зануляем ключ in-place (S8); шифрование в полёте работает с
            # собственной копией (см. _key_material).
            self._zero(self._dek)
            self._dek = None
        self._dirty = False
        self._bump_session()

    def _mark_dirty(self):
        """Пометить БД как изменённую и уведомить контроллер (для отложенной
        записи). В незашифрованном режиме запись не нужна — данные уже в файле."""
        self._dirty = True
        if self.encrypted and self._on_dirty is not None:
            self._on_dirty()

    def flush(self, force: bool = False):
        """Сбросить накопленные изменения на диск (если есть). Вызывается
        контроллером по таймеру и в финальных точках. force — перезаписать,
        даже если файл изменён извне (см. VaultConflictError)."""
        if self._dirty:
            # Флаг снимаем ДО записи: мутатор, пометивший БД грязной во время
            # persist, иначе был бы затёрт присваиванием False после записи
            # (его правки остались бы несохранёнными без повторного flush).
            # При сбое возвращаем флаг (как VaultController._start_vault_write).
            self._dirty = False
            try:
                self.persist(force=force)
            except BaseException:
                self._dirty = True
                raise

    def _commit(self):
        """Зафиксировать транзакцию и пометить БД грязной (отложенная запись
        в шифрованном режиме; в обычном — no-op, файл уже на диске)."""
        self.conn.commit()
        self._mark_dirty()

    def _write(self, sql, params=()):
        """Выполнить один мутирующий statement в явной транзакции и пометить БД
        грязной (M-6). `with self.conn` фиксирует при успехе и откатывает при
        исключении; семантика dirty идентична _commit (commit + _mark_dirty)."""
        with self.conn:
            self.cursor.execute(sql, params)
        self._mark_dirty()

    # ─── Управление шифрованием ──────────────────────────────────────────────

    def _atomic_write(self, data: bytes):
        # Используется при включении/выключении шифрования (интерактивно,
        # состояние файла под контролем) — пишем без проверки конфликта, но
        # ревизию обновляем, чтобы последующие persist() сверялись корректно.
        with self._write_lock:
            self._atomic_replace(data)

    def _atomic_write_wiping_old(self, data: bytes):
        """Как _atomic_write, но затирает прежнее содержимое файла (best-effort).

        os.replace отдаёт старые блоки файловой системе нетронутыми - plaintext
        остаётся на диске в свободных секторах. Поэтому ПЕРЕД заменой берём
        жёсткую ссылку на старый inode; после успешной замены (контейнер уже
        durable) затираем нулями именно его блоки и удаляем ссылку. Нет жёстких
        ссылок (ФС/права) - просто обычная замена. Сбой замены: ссылку убираем
        простым unlink (файл жив - затирать его нельзя)."""
        hold = f"{self.db_path}.pre-update-wipe-{os.getpid()}"
        linked = False
        self._wipe_hold_active = True
        try:
            try:
                if os.path.exists(hold):
                    os.unlink(hold)
                os.link(self.db_path, hold)
                linked = True
            except OSError:
                linked = False
            try:
                self._atomic_write(data)
            except BaseException:
                if linked:
                    try:
                        os.unlink(hold)
                    except OSError:
                        pass
                raise
            if linked:
                best_effort_wipe(hold)
        finally:
            self._wipe_hold_active = False

    def enable_encryption(self, password: str, preset: str) -> str:
        """Зашифровать текущую (обычную) БД. Возвращает recovery-код.
        После вызова БД работает в зашифрованном режиме (в памяти).

        Порядок важен: контейнер сначала собирается и ПРОВЕРЯЕТСЯ (cs.unlock) —
        и только потом закрывается рабочее соединение. Если запись файла упадёт,
        переоткрываем обычную БД, чтобы объект не остался с закрытым соединением
        в несогласованном состоянии (раньше так и было — все CRUD падали)."""
        db_bytes = self.conn.serialize()
        container, recovery = cs.create_vault(db_bytes, password, preset)
        pt, dek, header = cs.unlock(container, password)  # валидируем ДО закрытия
        self.conn.close()
        try:
            self._atomic_write_wiping_old(container)
        except Exception:
            self.connect()   # старый файл не тронут — возвращаем рабочий режим
            raise
        self.open_encrypted(pt, dek, header)
        self._wipe_plaintext_leftovers()
        return recovery

    def _wipe_plaintext_leftovers(self):
        """S7: после включения шифрования затереть plaintext-копии этой БД рядом
        с файлом: `.pre-migrate*` и `.pre-update*` (не контейнеры) и хвосты
        SQLite. Best-effort;
        бэкапы лежат в другой папке и здесь не трогаются."""
        directory = os.path.dirname(self.db_path) or "."
        base = os.path.basename(self.db_path)
        try:
            names = os.listdir(directory)
        except OSError:
            return
        for name in names:
            if name in (base + "-journal", base + "-wal", base + "-shm") or \
                    name.startswith((base + ".pre-migrate", base + ".pre-update")):
                path = os.path.join(directory, name)
                if os.path.isfile(path) and not cs.is_encrypted_file(path):
                    best_effort_wipe(path)

    def disable_encryption(self) -> None:
        """Расшифровать БД обратно в обычный файл и работать без шифрования."""
        db_bytes = self.conn.serialize()
        dek, header = self._dek, self._header
        # open_encrypted сбрасывает _dirty - запоминаем, чтобы при сбое не
        # потерять несохранённые правки.
        was_dirty = self._dirty
        self.conn.close()
        try:
            # Сериализованные байты — это валидный файл SQLite; пишем как есть.
            self._atomic_write(db_bytes)
        except Exception:
            # Запись не удалась (на диске остался зашифрованный контейнер) —
            # возвращаем рабочее зашифрованное состояние из байтов в памяти.
            self.open_encrypted(db_bytes, dek, header)
            self._dirty = was_dirty
            if was_dirty:
                self._mark_dirty()   # заново запланировать отложенную запись
            self._zero(dek)
            raise
        self._zero(dek)
        self.connect()

    def change_master_password(self, new_password: str, preset: str | None = None,
                               rotate_dek: bool = False):
        """Сменить мастер-пароль (перезаворачивание DEK, без перешифровки данных).

        rotate_dek=True (S2) - ещё и новый DEK (данные перешифровываются при
        сохранении) и новый recovery-код: возвращает его (показать один раз).
        Иначе возвращает None. Ключ и заголовок применяются вместе и
        откатываются, если запись на диск не удалась."""
        dek, header = self._key_material()
        if not rotate_dek:
            self.set_header(cs.change_password(None, dek, header,
                                               new_password, preset))
            return None
        new_header, new_dek, recovery = cs.change_password(
            None, dek, header, new_password, preset, rotate_dek=True)
        old_dek, old_header = self._dek, self._header
        with self._key_lock:
            self._dek, self._header = bytearray(new_dek), new_header
        try:
            self.persist()
        except BaseException:
            with self._key_lock:
                self._zero(self._dek)
                self._dek, self._header = old_dek, old_header
            raise
        self._zero(old_dek)
        return recovery

    def regenerate_recovery_code(self) -> str:
        """Сгенерировать новый recovery-код. Возвращает код (показать один раз)."""
        dek, header = self._key_material()
        new_header, recovery = cs.regenerate_recovery(dek, header)
        self.set_header(new_header)
        return recovery

    def verify_secret(self, secret: str, is_recovery: bool = False) -> bool:
        """Проверить мастер-пароль ИЛИ recovery-код против текущего заголовка.

        Доступ возможен любым секретом, так как оба заворачивают один и тот же
        DEK — поэтому смену пароля / отключение шифрования можно авторизовать
        и паролем, и recovery-кодом."""
        if not self.encrypted or self._header is None:
            return False
        return cs.check_secret(self._header, secret, is_recovery)

    def verify_password(self, password: str) -> bool:
        """Обратная совместимость: проверка только мастер-пароля."""
        return self.verify_secret(password, is_recovery=False)

    def close(self, persist=True, force=False):
        """Закрытие соединения. persist=False — закрыть БЕЗ сохранения на диск
        (нужно при восстановлении бэкапа, чтобы не затереть восстановленный файл
        текущей in-memory базой). force — перезаписать файл, изменённый извне."""
        if persist and self.encrypted and self.conn is not None and self._dek is not None:
            # Сбой записи пробрасываем наверх (см. lock): закрытие без
            # подтверждённого сохранения тихо теряло бы последние изменения.
            self.persist(force=force)
            self._dirty = False
        if self.conn:
            self.conn.close()
        self._bump_session()
