import sqlite3
import os
import tempfile
import asyncio
import threading
import functools
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from domain import days_until_password_change, canonical_link_pair


# Версия схемы базы данных. Увеличивается при изменении структуры таблиц,
# чтобы _migrate() мог обновить существующие документы пользователей.
# v6: единоразовый прогон полной нормализации (дедуп + UNIQUE-индексы) для
#     старых баз и переход на быстрый старт (см. create_tables: если версия
#     актуальна — миграция/дедуп пропускаются).
# v7: канонизация связей — хранить только пары (min,max) и закрепить инвариант
#     CHECK(account_id < linked_account_id) пересборкой таблицы linked_accounts.
SCHEMA_VERSION = 7

# Обязательные таблицы актуальной схемы. На «быстром пути» create_tables() даже
# при совпадении версии проверяет их наличие (M6-06): частично повреждённую базу
# нельзя принимать слепо — недостающие таблицы будут пересозданы.
_REQUIRED_TABLES = frozenset({
    "folders", "services", "accounts", "personal_data", "secret_questions",
    "recovery_phrases", "recovery_codes", "gallery", "linked_accounts", "app_meta",
})


class FutureSchemaError(Exception):
    """База создана более новой версией программы (её схема новее поддерживаемой).
    Открывать такую базу нельзя: «миграция вниз» повредила бы данные."""

    def __init__(self, found, supported):
        self.found = found
        self.supported = supported
        super().__init__(
            f"База создана более новой версией Хранилки (схема {found}, "
            f"поддерживается {supported}). Обновите программу."
        )


class VaultConflictError(Exception):
    """Файл-БД на диске изменился извне (другой программой, синхронизацией,
    восстановлением) с момента, как мы его открыли/последний раз сохранили.
    Перезапись затёрла бы чужие изменения — поэтому требуется решение пользователя."""


class StaleSessionError(Exception):
    """Фоновая run_async-операция относится к уже закрытой/сменённой сессии БД
    (между постановкой в очередь и выполнением произошёл close/lock/restore).
    Вызыватель должен трактовать это как устаревший результат и не применять его."""


class Database:
    def __init__(self, db_path: str = "hranilka.db") -> None:
        self.db_path = db_path
        # conn/cursor — None вне открытой сессии (до connect()/после lock()); все
        # публичные методы вызываются при открытой БД (см. relax в mypy.ini).
        self.conn: sqlite3.Connection | None = None
        self.cursor: sqlite3.Cursor | None = None
        # «Ревизия» файла на диске на момент открытия/последней нашей записи
        # (st_mtime_ns, размер). Перед перезаписью сверяем — если отличается,
        # значит файл изменили извне (см. VaultConflictError).
        self._disk_revision: tuple[int, int] | None = None
        # Состояние шифрования. Когда encrypted=True, БД живёт в sqlite :memory:,
        # а на диске лежит зашифрованный контейнер (см. crypto_store).
        self.encrypted = False
        self._dek: bytes | None = None       # ключ данных (расшифрованный), только в памяти
        self._header: dict | None = None     # заголовок контейнера (соли, обёрнутые DEK)
        # Отложенная запись на диск (только шифр. режим): мутаторы помечают БД
        # «грязной», а контроллер сбрасывает её один раз за оборот событийного
        # цикла. _on_dirty — callback контроллера (или None), вызывается при
        # появлении несохранённых изменений в шифрованном режиме.
        self._dirty = False
        self._on_dirty = None
        # ─── Асинхронный доступ к БД ──────────────────────────────────────────
        # Соединение SQLite открывается с check_same_thread=False, чтобы тяжёлые
        # операции (снапшот, чтение BLOB, запись карточки) можно было выполнять в
        # фоновом потоке-исполнителе через run_async() — UI-поток при этом не
        # подвисает. Единственный воркер (max_workers=1) сериализует async-операции
        # между собой, а реентрантный RLock — с синхронными вызовами из UI-потока
        # (каждый публичный метод обёрнут в этот лок, см. конец файла). Тяжёлый CPU
        # (AES-GCM, декодирование картинок) выполняется ВНЕ лока (в QThread писателя
        # и пуле картинок), поэтому лок удерживается лишь на время доступа к conn.
        self._lock = threading.RLock()
        self._executor: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="db-worker")
        # Поколение сессии БД: растёт при каждой смене соединения (connect/
        # open_encrypted/lock/close). Фоновая run_async-операция, поставленная до
        # close/lock/restore, не должна выполниться над уже другим conn (H6-03):
        # перед вызовом метода под локом сверяем поколение и прерываемся, если оно
        # сменилось. Так привилегированные операции (lock/restore) безопасны даже
        # при висящих в очереди фоновых задачах прежней сессии.
        self._session_gen = 0

    def _bump_session(self):
        """Отметить смену соединения — погасить фоновые операции прежней сессии."""
        self._session_gen += 1

    async def run_async(self, method, *args, **kwargs):
        """Выполнить синхронный метод БД в фоновом потоке, не блокируя UI-поток.

        method — публичный метод этого экземпляра (уже обёрнут локом). Результат
        возвращается обычным await. Пример: await db.run_async(db.load_account, id).

        Если за время ожидания в очереди сессия БД сменилась (close/lock/restore),
        метод НЕ выполняется — поднимается StaleSessionError (вызыватель трактует
        как «результат устарел»)."""
        loop = asyncio.get_running_loop()
        gen = self._session_gen

        def _call():
            # Сверка поколения и вызов — под одним локом: close/lock не вклинятся
            # между проверкой и работой метода (метод берёт тот же реентрантный лок).
            with self._lock:
                if self._session_gen != gen:
                    raise StaleSessionError()
                return method(*args, **kwargs)

        return await loop.run_in_executor(self._executor, _call)

    def shutdown_executor(self):
        """Остановить поток-исполнитель async-операций (идемпотентно). Вызывать на
        выходе из приложения, когда фоновых async-операций уже нет."""
        ex = getattr(self, "_executor", None)
        if ex is not None:
            self._executor = None
            ex.shutdown(wait=True)

    def _setup_conn(self):
        """Общие настройки соединения (row_factory, внешние ключи)."""
        self.conn.row_factory = sqlite3.Row  # Чтобы обращаться к полям по имени
        # Без этого ON DELETE CASCADE/SET NULL не работают в SQLite.
        self.conn.execute("PRAGMA foreign_keys = ON")
        # Затирать содержимое удаляемых страниц нулями, а не просто помечать их
        # свободными: иначе удалённые пароли/BLOB остаются читаемыми в файле БД
        # до следующего VACUUM (Баг 2). В in-memory (шифр.) режиме безвреден.
        self.conn.execute("PRAGMA secure_delete = ON")
        self.cursor = self.conn.cursor()

    def connect(self) -> None:
        """Открыть обычную (незашифрованную) базу — файл на диске."""
        self.encrypted = False
        self._dek = None
        self._header = None
        self._dirty = False
        # check_same_thread=False: доступ из UI-потока и из воркера run_async
        # сериализуется RLock'ом (см. __init__), поэтому безопасно.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._disk_revision = self._stat_revision()
        self._setup_conn()
        self._bump_session()

    def open_encrypted(self, db_bytes: bytes, dek: bytes, header: dict) -> None:
        """Открыть расшифрованные байты БД в памяти. Файл остаётся шифрованным;
        изменения сбрасываются на диск через persist()."""
        self.encrypted = True
        self._dek = dek
        self._header = header
        self._dirty = False
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        if db_bytes:
            self.conn.deserialize(db_bytes)  # type: ignore[attr-defined]  # есть в CPython 3.11+
        self._disk_revision = self._stat_revision()
        self._setup_conn()
        self._bump_session()

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
            raise VaultConflictError()

    def _atomic_replace(self, data: bytes):
        """Атомарно заменить файл-БД содержимым data через УНИКАЛЬНЫЙ временный
        файл в том же каталоге (tempfile.mkstemp + fsync + os.replace).

        Уникальное имя temp на каждую запись принципиально (H3-01): при общем
        фиксированном `db_path + ".tmp"` две одновременные записи (фоновый
        воркер + синхронная привилегированная операция) затирали бы временный
        файл друг друга, давая порчу/потерю данных. mkstemp гарантирует, что у
        каждой записи свой temp. Ревизию диска обновляем после успешной замены."""
        directory = os.path.dirname(self.db_path) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".hranilka-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
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

    def _write_container(self, container: bytes, force: bool = False):
        """Атомарно записать готовый контейнер на диск (temp+fsync+replace).
        Перед заменой сверяет ревизию файла (если не force) и обновляет её после."""
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
        import crypto_store as cs
        return cs.seal(self.serialize_db(), self._dek, self._header)

    def seal_and_write(self, db_bytes: bytes, force: bool = False):
        """Зашифровать ГОТОВЫЕ байты БД и атомарно записать контейнер.

        Не обращается к соединению SQLite — безопасно вызывать из рабочего
        потока (см. фоновую запись в main.py). Шифрование (AES-GCM) и запись —
        самые тяжёлые части сохранения."""
        import crypto_store as cs
        container = cs.seal(db_bytes, self._dek, self._header)
        self._write_container(container, force=force)

    def persist(self, force: bool = False):
        """Сбросить текущее состояние БД на диск.

        В обычном режиме — no-op (SQLite уже пишет в файл). В зашифрованном —
        сериализует in-memory БД, шифрует и атомарно записывает контейнер.
        VaultConflictError, если файл изменён извне (force=True — перезаписать)."""
        if not self.encrypted or self.conn is None or self._dek is None:
            return
        self._write_container(self.serialize_container(), force=force)

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
            self.persist(force=force)
            self._dirty = False

    def _commit(self):
        """Зафиксировать транзакцию и пометить БД грязной (отложенная запись
        в шифрованном режиме; в обычном — no-op, файл уже на диске)."""
        self.conn.commit()
        self._mark_dirty()

    # ─── Управление шифрованием ──────────────────────────────────────────────

    def _atomic_write(self, data: bytes):
        # Используется при включении/выключении шифрования (интерактивно,
        # состояние файла под контролем) — пишем без проверки конфликта, но
        # ревизию обновляем, чтобы последующие persist() сверялись корректно.
        self._atomic_replace(data)

    def enable_encryption(self, password: str, preset: str) -> str:
        """Зашифровать текущую (обычную) БД. Возвращает recovery-код.
        После вызова БД работает в зашифрованном режиме (в памяти).

        Порядок важен: контейнер сначала собирается и ПРОВЕРЯЕТСЯ (cs.unlock) —
        и только потом закрывается рабочее соединение. Если запись файла упадёт,
        переоткрываем обычную БД, чтобы объект не остался с закрытым соединением
        в несогласованном состоянии (раньше так и было — все CRUD падали)."""
        import crypto_store as cs
        db_bytes = self.conn.serialize()
        container, recovery = cs.create_vault(db_bytes, password, preset)
        pt, dek, header = cs.unlock(container, password)  # валидируем ДО закрытия
        self.conn.close()
        try:
            self._atomic_write(container)
        except Exception:
            self.connect()   # старый файл не тронут — возвращаем рабочий режим
            raise
        self.open_encrypted(pt, dek, header)
        return recovery

    def disable_encryption(self) -> None:
        """Расшифровать БД обратно в обычный файл и работать без шифрования."""
        db_bytes = self.conn.serialize()
        dek, header = self._dek, self._header
        self.conn.close()
        try:
            # Сериализованные байты — это валидный файл SQLite; пишем как есть.
            self._atomic_write(db_bytes)
        except Exception:
            # Запись не удалась (на диске остался зашифрованный контейнер) —
            # возвращаем рабочее зашифрованное состояние из байтов в памяти.
            self.open_encrypted(db_bytes, dek, header)
            raise
        self.connect()

    def change_master_password(self, new_password: str, preset: str | None = None):
        """Сменить мастер-пароль (перезаворачивание DEK, без перешифровки данных)."""
        import crypto_store as cs
        new_header = cs.change_password(None, self._dek, self._header,
                                        new_password, preset)
        self.set_header(new_header)

    def regenerate_recovery_code(self) -> str:
        """Сгенерировать новый recovery-код. Возвращает код (показать один раз)."""
        import crypto_store as cs
        new_header, recovery = cs.regenerate_recovery(self._dek, self._header)
        self.set_header(new_header)
        return recovery

    def verify_secret(self, secret: str, is_recovery: bool = False) -> bool:
        """Проверить мастер-пароль ИЛИ recovery-код против текущего заголовка.

        Доступ возможен любым секретом, так как оба заворачивают один и тот же
        DEK — поэтому смену пароля / отключение шифрования можно авторизовать
        и паролем, и recovery-кодом."""
        import crypto_store as cs
        if not self.encrypted or self._header is None:
            return False
        try:
            if is_recovery:
                cs._unwrap(self._header["wrap_rec"],
                           cs.normalize_recovery_code(secret),
                           self._header["salt_rec"], self._header["params"])
            else:
                cs._unwrap(self._header["wrap_pw"], secret,
                           self._header["salt_pw"], self._header["params"])
            return True
        except cs.WrongPassword:
            return False

    def verify_password(self, password: str) -> bool:
        """Обратная совместимость: проверка только мастер-пароля."""
        return self.verify_secret(password, is_recovery=False)

    def create_tables(self):
        """Создание всех таблиц согласно ТЗ"""

        # H3-03 + ускорение запуска: версию схемы определяем СНАЧАЛА и read-only,
        # до любого DDL/DML/commit. (1) Базу более новой версии нельзя изменять
        # перед отказом в открытии; (2) актуальную базу не нужно повторно
        # нормализовать/мигрировать — это заметно сокращает работу при старте.
        existing = self._table_names()
        if "app_meta" in existing:
            _ver = self.get_schema_version()
        elif existing:
            _ver = 0            # старая база без app_meta — нужна миграция
        else:
            _ver = None         # пустая новая база — создаём с нуля
        if _ver is not None and _ver > SCHEMA_VERSION:
            raise FutureSchemaError(_ver, SCHEMA_VERSION)
        if _ver == SCHEMA_VERSION and _REQUIRED_TABLES.issubset(existing):
            return              # схема актуальна и все таблицы на месте — делать нечего
        # M6-06: даже при «актуальной» версии не доверяем ей слепо — если
        # обязательной таблицы нет (частично повреждённая база), НЕ возвращаемся
        # рано, а проходим ниже CREATE TABLE IF NOT EXISTS и восстанавливаем её.
        # Полный foreign_key_check на горячем пути не запускаем (дорого на больших
        # базах); целостность связей проверяется отдельной операцией.

        # Таблица папок
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sort_order INTEGER DEFAULT 0
            )
        """)
        
        # Таблица сервисов
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                folder_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sort_order INTEGER DEFAULT 0,
                FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE SET NULL
            )
        """)
        
        # Таблица аккаунтов
        # created_at      — служебная метка вставки строки (не редактируется пользователем)
        # creation_date   — дата регистрации аккаунта (поле "Дата создания" из ТЗ, редактируемое)
        # service_id допускает NULL: аккаунт может быть "свободным" (без сервиса)
        # после удаления сервиса "с сохранением содержимого".
        self.create_tables_accounts_only()
        
        # Таблица персональных данных
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS personal_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                first_name TEXT,
                last_name TEXT,
                middle_name TEXT,
                birth_date DATE,
                address TEXT,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        
        # Таблица секретных вопросов (до 5 на аккаунт)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS secret_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        
        # Таблица фразы восстановления
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS recovery_phrases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                phrase TEXT,
                device_id TEXT,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        
        # Таблица одноразовых кодов (backup codes для 2FA)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS recovery_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        
        # Таблица галереи (картинки, чеки, инвойсы)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS gallery (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                description TEXT,
                image_data BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        
        # Таблица связанных аккаунтов (пары канонизированы: account_id < linked,
        # инвариант закреплён CHECK — см. _linked_accounts_create_sql).
        self.cursor.execute(
            self._linked_accounts_create_sql("linked_accounts", if_not_exists=True))

        # Таблица служебных метаданных (версия схемы, и т.п.)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Канонизация существующей таблицы связей (v6→v7): нормализация пар к
        # (min,max), дедуп и добавление CHECK. Выполняется ДО создания индексов
        # ниже, чтобы uq_linked_pair лёг уже на пересобранную таблицу.
        self._rebuild_linked_accounts_if_needed()

        # Перед созданием UNIQUE-индексов убираем возможные дубли (из старых баз
        # или ручных правок), иначе создание уникального индекса упадёт.
        # personal_data / recovery_phrases — не более одной строки на аккаунт.
        self.cursor.execute(
            "DELETE FROM personal_data WHERE id NOT IN "
            "(SELECT MIN(id) FROM personal_data GROUP BY account_id)")
        self.cursor.execute(
            "DELETE FROM recovery_phrases WHERE id NOT IN "
            "(SELECT MIN(id) FROM recovery_phrases GROUP BY account_id)")
        # linked_accounts: убрать самоссылки и неупорядоченные дубли (A,B)/(B,A).
        self.cursor.execute("DELETE FROM linked_accounts WHERE account_id = linked_account_id")
        self.cursor.execute(
            "DELETE FROM linked_accounts WHERE id NOT IN ("
            " SELECT MIN(id) FROM linked_accounts "
            " GROUP BY MIN(account_id, linked_account_id), MAX(account_id, linked_account_id))")
        # Старые НЕуникальные индексы (если успели создаться) заменяем уникальными.
        self.cursor.execute("DROP INDEX IF EXISTS idx_personal_account")
        self.cursor.execute("DROP INDEX IF EXISTS idx_phrases_account")

        # Индексы по внешним ключам и частым фильтрам. CREATE INDEX IF NOT EXISTS
        # идемпотентен, поэтому безопасно выполняется при каждом открытии и
        # автоматически появляется в уже существующих базах (миграция не нужна).
        # UNIQUE-индексы заодно дают целостность (M-14): не более одной строки
        # ПД/фразы на аккаунт и отсутствие дублирующихся связей.
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_services_folder ON services(folder_id)",
            "CREATE INDEX IF NOT EXISTS idx_accounts_service ON accounts(service_id)",
            "CREATE INDEX IF NOT EXISTS idx_accounts_deleted ON accounts(deleted_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_personal_account ON personal_data(account_id)",
            "CREATE INDEX IF NOT EXISTS idx_questions_account ON secret_questions(account_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_phrases_account ON recovery_phrases(account_id)",
            "CREATE INDEX IF NOT EXISTS idx_codes_account ON recovery_codes(account_id)",
            "CREATE INDEX IF NOT EXISTS idx_gallery_account ON gallery(account_id)",
            "CREATE INDEX IF NOT EXISTS idx_linked_account ON linked_accounts(account_id)",
            "CREATE INDEX IF NOT EXISTS idx_linked_linked ON linked_accounts(linked_account_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_linked_pair ON linked_accounts(account_id, linked_account_id)",
        ):
            self.cursor.execute(stmt)

        self._commit()

        # Доработка существующих баз до актуальной схемы
        self._migrate()

    # Ожидаемые колонки таблиц для добавления в старые базы (имя -> SQL-определение).
    # Используется только в _migrate(); новые базы создаются полными в create_tables().
    _EXPECTED_COLUMNS = {
        "accounts": {
            "url": "TEXT",
            "creation_date": "TIMESTAMP",
            "password_changed_date": "DATE",
            "password_change_interval_days": "INTEGER",
            "notes": "TEXT",
            "ip": "TEXT",
            "browser": "TEXT",
            "os": "TEXT",
            "extra_info": "TEXT",
            "is_favorite": "INTEGER DEFAULT 0",
            "sort_order": "INTEGER DEFAULT 0",
            "deleted_at": "TIMESTAMP",
        },
    }

    def _get_columns(self, table):
        """Возвращает множество имён колонок таблицы."""
        self.cursor.execute(f"PRAGMA table_info({table})")
        return {row["name"] for row in self.cursor.fetchall()}

    def _migrate(self):
        """Приводит существующую базу к актуальной версии схемы.

        Добавляет недостающие колонки в старые базы (ALTER TABLE ADD COLUMN
        идемпотентен в рамках проверки) и обновляет версию схемы в app_meta.
        """
        current = self.get_schema_version()
        if current == SCHEMA_VERSION:
            return
        if current > SCHEMA_VERSION:
            # База создана более новой версией программы. Молчаливая «миграция»
            # вниз записала бы устаревшую версию схемы и могла бы необратимо
            # повредить данные — поэтому отказываемся открывать.
            raise FutureSchemaError(current, SCHEMA_VERSION)

        for table, columns in self._EXPECTED_COLUMNS.items():
            existing = self._get_columns(table)
            for name, definition in columns.items():
                if name not in existing:
                    self.cursor.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    )

        self._migrate_accounts_service_nullable()
        # Чинит базы, испорченные старой (ошибочной) версией миграции,
        # где ссылки внешних ключей указывали на несуществующую accounts_old.
        self._repair_accounts_old_refs()

        self.set_schema_version(SCHEMA_VERSION)
        self._commit()

    @staticmethod
    def _accounts_create_sql(table_name):
        """CREATE TABLE для accounts (service_id допускает NULL)."""
        return f"""
            CREATE TABLE {table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id INTEGER,
                account_name TEXT NOT NULL,
                url TEXT,
                login TEXT,
                password TEXT,
                creation_date TIMESTAMP,
                password_changed_date DATE,
                password_change_interval_days INTEGER,
                notes TEXT,
                ip TEXT,
                browser TEXT,
                os TEXT,
                extra_info TEXT,
                is_favorite INTEGER DEFAULT 0,
                sort_order INTEGER DEFAULT 0,
                deleted_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE CASCADE
            )
        """

    def create_tables_accounts_only(self):
        """Создаёт таблицу accounts, если её ещё нет."""
        if "accounts" not in self._table_names():
            self.cursor.execute(self._accounts_create_sql("accounts"))

    @staticmethod
    def _linked_accounts_create_sql(table_name, if_not_exists=False):
        """CREATE TABLE для linked_accounts.

        Пары канонизированы: всегда account_id < linked_account_id. Инвариант
        закреплён CHECK — он же делает невозможным повторное появление обратных
        дублей (B,A) и самоссылок (A,A) на уровне схемы (M3-06/M3-14)."""
        ine = "IF NOT EXISTS " if if_not_exists else ""
        return f"""
            CREATE TABLE {ine}{table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                linked_account_id INTEGER NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                FOREIGN KEY (linked_account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                CHECK (account_id < linked_account_id)
            )
        """

    def _rebuild_linked_accounts_if_needed(self):
        """Привести таблицу связей к канонической форме (v6→v7), если ещё нет.

        Старые базы хранили (A,B) и (B,A) как разные строки и без инварианта.
        Пересобираем таблицу с CHECK, по пути нормализуя порядок к (min,max),
        убирая самоссылки и дубли. Для уже канонизированной таблицы (CHECK уже
        присутствует) — no-op, так что повторные запуски ничего не делают."""
        self.cursor.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='linked_accounts'")
        row = self.cursor.fetchone()
        if not row or not row["sql"]:
            return
        if "account_id < linked_account_id" in row["sql"]:
            return  # инвариант уже закреплён — таблица канонична

        self._commit()  # закрыть возможную открытую транзакцию (иначе PRAGMA игнорируется)
        self.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with self.conn:
                self.cursor.execute(
                    self._linked_accounts_create_sql("linked_accounts_new"))
                # Нормализуем к (min,max), отбрасываем самоссылки и дубли (A,B)/(B,A).
                self.cursor.execute(
                    "INSERT OR IGNORE INTO linked_accounts_new "
                    "(account_id, linked_account_id) "
                    "SELECT MIN(account_id, linked_account_id), "
                    "       MAX(account_id, linked_account_id) "
                    "FROM linked_accounts WHERE account_id != linked_account_id "
                    "GROUP BY MIN(account_id, linked_account_id), "
                    "         MAX(account_id, linked_account_id)")
                self.cursor.execute("DROP TABLE linked_accounts")
                self.cursor.execute(
                    "ALTER TABLE linked_accounts_new RENAME TO linked_accounts")
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")

    def _table_names(self):
        self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {r["name"] for r in self.cursor.fetchall()}

    def _migrate_accounts_service_nullable(self):
        """Снимает ограничение NOT NULL с accounts.service_id, если оно есть.

        Безопасный порядок (по документации SQLite): создаём новую таблицу под
        ВРЕМЕННЫМ именем, копируем данные, удаляем старую, переименовываем новую.
        Так SQLite не переписывает внешние ключи дочерних таблиц на временное имя."""
        self.cursor.execute("PRAGMA table_info(accounts)")
        info = {row["name"]: row for row in self.cursor.fetchall()}
        if "service_id" not in info or info["service_id"]["notnull"] == 0:
            return  # уже nullable (или таблицы ещё нет)

        cols = ", ".join(info.keys())
        self._commit()  # закрыть возможную открытую транзакцию (иначе PRAGMA игнорируется)
        self.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with self.conn:
                self.cursor.execute(self._accounts_create_sql("accounts_new"))
                self.cursor.execute(
                    f"INSERT INTO accounts_new ({cols}) SELECT {cols} FROM accounts"
                )
                self.cursor.execute("DROP TABLE accounts")
                self.cursor.execute("ALTER TABLE accounts_new RENAME TO accounts")
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")

    def _repair_accounts_old_refs(self):
        """Чинит дочерние таблицы, чьи внешние ключи ссылаются на accounts_old
        (последствие старой ошибочной миграции). Пересобирает их с корректными
        ссылками на accounts, сохраняя данные."""
        self.cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")
        broken = [(r["name"], r["sql"]) for r in self.cursor.fetchall()
                  if r["sql"] and "accounts_old" in r["sql"]]
        if not broken:
            return

        self._commit()  # закрыть возможную открытую транзакцию (иначе PRAGMA игнорируется)
        self.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with self.conn:
                for name, sql in broken:
                    cols = ", ".join(self._get_columns(name))
                    tmp = name + "_fix_tmp"
                    # Исправляем ссылку и создаём временную таблицу с корректной схемой
                    fixed_sql = sql.replace("accounts_old", "accounts").replace(name, tmp, 1)
                    self.cursor.execute(fixed_sql)
                    self.cursor.execute(f"INSERT INTO {tmp} ({cols}) SELECT {cols} FROM {name}")
                    self.cursor.execute(f"DROP TABLE {name}")
                    self.cursor.execute(f"ALTER TABLE {tmp} RENAME TO {name}")
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")
        # Подчистим возможный осиротевший остаток
        if "accounts_old" in self._table_names():
            self.cursor.execute("DROP TABLE accounts_old")

    def get_schema_version(self):
        """Текущая версия схемы (0, если ещё не установлена)."""
        self.cursor.execute("SELECT value FROM app_meta WHERE key = 'schema_version'")
        row = self.cursor.fetchone()
        return int(row["value"]) if row else 0

    def set_schema_version(self, version):
        self.cursor.execute(
            "INSERT INTO app_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(version),),
        )

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

    def wipe_all_data(self):
        """Удаляет все пользовательские данные, сохраняя структуру таблиц.

        Удаляем от дочерних таблиц к родительским, поэтому внешние ключи можно
        не отключать. Раньше код вызывал `PRAGMA foreign_keys = OFF/ON`, но
        PRAGMA внутри неявной транзакции (её открывают DELETE) игнорируется, и
        после очистки FK оставались ВЫКЛЮЧЕННЫМИ до перезапуска — все
        последующие ON DELETE CASCADE/SET NULL переставали работать."""
        with self.conn:
            for table in ("gallery", "recovery_codes", "recovery_phrases",
                           "secret_questions", "personal_data", "linked_accounts",
                           "accounts", "services", "folders"):
                self.conn.execute(f"DELETE FROM {table}")
        self._mark_dirty()
            
    def add_folder(self, name: str) -> int:
        """Добавление папки"""
        self.cursor.execute(
            "INSERT INTO folders (name) VALUES (?)",
            (name,)
        )
        self._commit()
        return int(self.cursor.lastrowid)
    
    def add_service(self, name: str, folder_id: int | None = None) -> int:
        """Добавление сервиса"""
        self.cursor.execute(
            "INSERT INTO services (name, folder_id) VALUES (?, ?)",
            (name, folder_id)
        )
        self._commit()
        return int(self.cursor.lastrowid)
    
    def add_account(self, service_id: int | None, account_name: str,
                    login: str | None = None, password: str | None = None) -> int:
        """Добавление аккаунта"""
        self.cursor.execute(
            "INSERT INTO accounts (service_id, account_name, login, password, created_at) VALUES (?, ?, ?, ?, ?)",
            (service_id, account_name, login, password, datetime.now())
        )
        self._commit()
        return int(self.cursor.lastrowid)
    
    def _build_account_node(self, account):
        """Формирует узел аккаунта для дерева, включая дни до смены пароля."""
        return {
            'type': 'account',
            'id': account['id'],
            'name': account['account_name'],
            'login': account['login'],
            'is_favorite': bool(account['is_favorite']),
            'password_changed_date': account['password_changed_date'],
            'password_change_interval_days': account['password_change_interval_days'],
            'pwd_days_left': days_until_password_change(
                account['password_changed_date'],
                account['password_change_interval_days'],
            ),
        }

    def _sorted_accounts(self, accounts, sort_mode, descending=False):
        """Сортирует список аккаунтов согласно режиму. Избранные всегда сверху."""
        nodes = [self._build_account_node(a) for a in accounts]

        if sort_mode == "name":
            nodes.sort(key=lambda n: n['name'].lower(), reverse=descending)
        elif sort_mode == "created":
            pass  # порядок задан в SQL (с учётом направления)
        elif sort_mode == "pwd_due":
            nodes.sort(key=lambda n: (n['pwd_days_left'] is None,
                                      n['pwd_days_left'] if n['pwd_days_left'] is not None else 0),
                       reverse=descending)
        # "manual" — порядок из SQL (по sort_order)

        # Избранные поднимаем наверх, сохраняя относительный порядок
        nodes.sort(key=lambda n: not n['is_favorite'])
        return nodes

    def _fetch_accounts(self, where_clause, params, sort_mode, descending):
        if sort_mode == "created":
            order = "created_at " + ("DESC" if descending else "ASC")
        else:
            order = "sort_order, account_name"
        # Аккаунты в корзине (deleted_at не пуст) в дереве не показываем.
        self.cursor.execute(
            f"SELECT * FROM accounts WHERE ({where_clause}) AND deleted_at IS NULL "
            f"ORDER BY {order}", params
        )
        return self._sorted_accounts(self.cursor.fetchall(), sort_mode, descending)

    @staticmethod
    def _container_order(sort_mode, descending):
        """ORDER BY для папок/сервисов: ручной режим — по sort_order, иначе по имени."""
        if sort_mode == "manual":
            return "sort_order, name COLLATE NOCASE"
        return "name COLLATE NOCASE " + ("DESC" if descending else "ASC")

    def get_tree_structure(self, sort_mode="manual", descending=False):
        """Структура дерева. sort_mode: manual | name | created | pwd_due.
        Верхний уровень: папки, затем сервисы вне папок, затем свободные аккаунты.

        Один запрос на тип (папки/сервисы/аккаунты) вместо запроса на каждый
        контейнер — устранение N+1 (на больших базах было десятки SELECT'ов)."""
        order = self._container_order(sort_mode, descending)
        if sort_mode == "created":
            acc_order = "created_at " + ("DESC" if descending else "ASC")
        else:
            acc_order = "sort_order, account_name"

        self.cursor.execute(f"SELECT * FROM folders ORDER BY {order}")
        folders = self.cursor.fetchall()
        self.cursor.execute(f"SELECT * FROM services ORDER BY {order}")
        services = self.cursor.fetchall()
        # Аккаунты в корзине (deleted_at не пуст) в дереве не показываем.
        self.cursor.execute(
            f"SELECT * FROM accounts WHERE deleted_at IS NULL ORDER BY {acc_order}"
        )
        accounts = self.cursor.fetchall()

        # Группируем в памяти, сохраняя порядок выборки (важно для режима
        # "created" и базового sort_order, поверх которых _sorted_accounts
        # доводит сортировку по имени/просрочке и поднимает избранные).
        accounts_by_service: dict[Any, list] = {}
        for a in accounts:
            accounts_by_service.setdefault(a["service_id"], []).append(a)
        services_by_folder: dict[Any, list] = {}
        for s in services:
            services_by_folder.setdefault(s["folder_id"], []).append(s)

        def acc_nodes(service_id):
            return self._sorted_accounts(
                accounts_by_service.get(service_id, []), sort_mode, descending)

        def service_node(s):
            return {'type': 'service', 'id': s['id'], 'name': s['name'],
                    'children': acc_nodes(s['id'])}

        result = []
        for folder in folders:
            result.append({
                'type': 'folder', 'id': folder['id'], 'name': folder['name'],
                'children': [service_node(s)
                             for s in services_by_folder.get(folder['id'], [])],
            })
        for s in services_by_folder.get(None, []):     # сервисы вне папок
            result.append(service_node(s))
        for node in acc_nodes(None):                     # свободные аккаунты
            result.append(node)
        return result

    # ----- Переименование -----

    def rename_folder(self, folder_id, name):
        self.cursor.execute("UPDATE folders SET name = ? WHERE id = ?", (name, folder_id))
        self._commit()

    def rename_service(self, service_id, name):
        self.cursor.execute("UPDATE services SET name = ? WHERE id = ?", (name, service_id))
        self._commit()

    # ----- Удаление без сохранения содержимого (полностью) -----

    def delete_folder(self, folder_id):
        """Удаляет папку вместе со всеми сервисами и их аккаунтами."""
        with self.conn:
            # services.folder_id имеет ON DELETE SET NULL, поэтому удаляем
            # сервисы явно (их аккаунты уйдут каскадом), затем папку.
            self.cursor.execute("SELECT id FROM services WHERE folder_id = ?", (folder_id,))
            for row in self.cursor.fetchall():
                self.cursor.execute("DELETE FROM services WHERE id = ?", (row["id"],))
            self.cursor.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        self._mark_dirty()

    def delete_service(self, service_id):
        """Удаляет сервис вместе с его аккаунтами (каскад по FK)."""
        self.cursor.execute("DELETE FROM services WHERE id = ?", (service_id,))
        self._commit()

    def delete_account(self, account_id):
        """Удаляет аккаунт и все связанные данные (каскад по FK)."""
        self.cursor.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        self._commit()

    # ----- Корзина (мягкое удаление аккаунтов) -----

    def _is_in_bin(self, account_id):
        """True, если аккаунт лежит в корзине (помечен как удалённый)."""
        self.cursor.execute(
            "SELECT deleted_at FROM accounts WHERE id = ?", (account_id,)
        )
        row = self.cursor.fetchone()
        return bool(row and row["deleted_at"])

    def move_account_to_bin(self, account_id):
        """Переносит аккаунт в корзину (мягкое удаление): данные сохраняются,
        но аккаунт скрыт из дерева и связей до восстановления."""
        self.cursor.execute(
            "UPDATE accounts SET deleted_at = ? WHERE id = ?",
            (datetime.now(), account_id),
        )
        self._commit()

    def restore_account(self, account_id):
        """Восстанавливает аккаунт из корзины."""
        self.cursor.execute(
            "UPDATE accounts SET deleted_at = NULL WHERE id = ?", (account_id,)
        )
        self._commit()

    def get_deleted_count(self):
        """Количество аккаунтов в корзине."""
        self.cursor.execute(
            "SELECT COUNT(*) AS n FROM accounts WHERE deleted_at IS NOT NULL"
        )
        return self.cursor.fetchone()["n"]

    def get_deleted_accounts(self):
        """Список аккаунтов в корзине (последние удалённые — сверху)."""
        self.cursor.execute(
            "SELECT id, account_name, deleted_at FROM accounts "
            "WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC, id DESC"
        )
        return [{"id": r["id"], "name": r["account_name"],
                 "deleted_at": r["deleted_at"]} for r in self.cursor.fetchall()]

    def empty_bin(self):
        """Безвозвратно удаляет все аккаунты из корзины."""
        self.cursor.execute("DELETE FROM accounts WHERE deleted_at IS NOT NULL")
        self._commit()

    # ----- Удаление с сохранением содержимого -----

    def delete_folder_keep_content(self, folder_id):
        """Удаляет папку, но её сервисы становятся самостоятельными (вне папки)."""
        with self.conn:
            self.cursor.execute(
                "UPDATE services SET folder_id = NULL WHERE folder_id = ?", (folder_id,)
            )
            self.cursor.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        self._mark_dirty()

    def delete_service_keep_content(self, service_id):
        """Удаляет сервис, но его аккаунты становятся свободными (без сервиса)."""
        with self.conn:
            self.cursor.execute(
                "UPDATE accounts SET service_id = NULL WHERE service_id = ?", (service_id,)
            )
            self.cursor.execute("DELETE FROM services WHERE id = ?", (service_id,))
        self._mark_dirty()

    # ----- Перемещение и избранное -----

    def _next_sort_order(self, table, parent_col, parent_id):
        """Следующий sort_order в конце списка для указанного контейнера."""
        if parent_id is None:
            self.cursor.execute(
                f"SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM {table} WHERE {parent_col} IS NULL"
            )
        else:
            self.cursor.execute(
                f"SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM {table} WHERE {parent_col} = ?",
                (parent_id,),
            )
        return self.cursor.fetchone()["n"]

    def move_service(self, service_id, folder_id):
        """Переносит сервис в папку (folder_id=None — вынести из папки), в конец списка."""
        order = self._next_sort_order("services", "folder_id", folder_id)
        self.cursor.execute(
            "UPDATE services SET folder_id = ?, sort_order = ? WHERE id = ?",
            (folder_id, order, service_id),
        )
        self._commit()

    def move_account(self, account_id, service_id):
        """Переносит аккаунт в сервис (service_id=None — сделать свободным), в конец списка."""
        order = self._next_sort_order("accounts", "service_id", service_id)
        self.cursor.execute(
            "UPDATE accounts SET service_id = ?, sort_order = ? WHERE id = ?",
            (service_id, order, account_id),
        )
        self._commit()

    def set_favorite(self, account_id, value):
        self.cursor.execute(
            "UPDATE accounts SET is_favorite = ? WHERE id = ?", (1 if value else 0, account_id)
        )
        self._commit()

    # ----- Сохранение порядка (для drag&drop) -----

    def set_folders_order(self, ordered_ids):
        """Записывает sort_order папкам в порядке переданного списка id."""
        with self.conn:
            for i, fid in enumerate(ordered_ids):
                self.cursor.execute("UPDATE folders SET sort_order = ? WHERE id = ?", (i, fid))
        self._mark_dirty()

    def set_services_order(self, ordered_ids):
        """Записывает sort_order сервисам в порядке переданного списка id."""
        with self.conn:
            for i, sid in enumerate(ordered_ids):
                self.cursor.execute("UPDATE services SET sort_order = ? WHERE id = ?", (i, sid))
        self._mark_dirty()

    def set_accounts_order(self, ordered_ids):
        """Записывает sort_order аккаунтам в порядке переданного списка id."""
        with self.conn:
            for i, aid in enumerate(ordered_ids):
                self.cursor.execute("UPDATE accounts SET sort_order = ? WHERE id = ?", (i, aid))
        self._mark_dirty()

    # ----- Списки для меню перемещения -----

    def get_folders(self):
        self.cursor.execute("SELECT id, name FROM folders ORDER BY name")
        return [{"id": r["id"], "name": r["name"]} for r in self.cursor.fetchall()]

    def get_services(self):
        self.cursor.execute("SELECT id, name FROM services ORDER BY name")
        return [{"id": r["id"], "name": r["name"]} for r in self.cursor.fetchall()]

    def get_descendant_account_ids(self, node_type, node_id):
        """id всех аккаунтов внутри папки/сервиса (для очистки кэша при удалении)."""
        if node_type == "service":
            self.cursor.execute("SELECT id FROM accounts WHERE service_id = ?", (node_id,))
        elif node_type == "folder":
            self.cursor.execute(
                "SELECT id FROM accounts WHERE service_id IN "
                "(SELECT id FROM services WHERE folder_id = ?)", (node_id,))
        else:
            return []
        return [r["id"] for r in self.cursor.fetchall()]

    # ----- Связанные аккаунты (двусторонние) и пути -----

    def get_account_path(self, account_id):
        """Человекочитаемый путь к аккаунту: 'Папка / Сервис / Аккаунт'."""
        self.cursor.execute(
            "SELECT account_name, service_id FROM accounts WHERE id = ?", (account_id,)
        )
        a = self.cursor.fetchone()
        if not a:
            return ""
        parts = []
        if a["service_id"]:
            self.cursor.execute(
                "SELECT name, folder_id FROM services WHERE id = ?", (a["service_id"],)
            )
            s = self.cursor.fetchone()
            if s:
                if s["folder_id"]:
                    self.cursor.execute(
                        "SELECT name FROM folders WHERE id = ?", (s["folder_id"],)
                    )
                    f = self.cursor.fetchone()
                    if f:
                        parts.append(f["name"])
                parts.append(s["name"])
        parts.append(a["account_name"])
        return " / ".join(parts)

    def get_all_accounts(self):
        """Все аккаунты с путями (для выбора в диалоге связывания).
        Аккаунты в корзине исключаются.

        Пути строятся из заранее загруженных карт имён (3 запроса всего), а не
        вызовом get_account_path() на каждый аккаунт (было N+1: до 1+3N запросов)."""
        acc, svc, fld = self._name_maps()
        rows = [{"id": aid, "name": self._path_from_maps(aid, acc, svc, fld)}
                for aid, (_name, _service_id, deleted) in acc.items() if not deleted]
        rows.sort(key=lambda r: r["name"].lower())
        return rows

    def gallery_total_bytes(self, exclude_account_id: int | None = None) -> int:
        """Суммарный объём всех картинок в галерее (в байтах).

        exclude_account_id — исключить указанный аккаунт из суммы: его картинки
        обычно держатся в памяти редактируемой карточки, и учитывать их повторно
        при проверке лимита суммарного объёма не нужно (M3-05)."""
        if exclude_account_id is None:
            self.cursor.execute(
                "SELECT COALESCE(SUM(LENGTH(image_data)), 0) AS s FROM gallery")
        else:
            self.cursor.execute(
                "SELECT COALESCE(SUM(LENGTH(image_data)), 0) AS s "
                "FROM gallery WHERE account_id != ?", (exclude_account_id,))
        return int(self.cursor.fetchone()["s"])

    def get_links(self, account_id: int) -> list[dict[str, Any]]:
        """Связанные аккаунты (в обе стороны) с путями."""
        self.cursor.execute(
            "SELECT account_id, linked_account_id FROM linked_accounts "
            "WHERE account_id = ? OR linked_account_id = ?",
            (account_id, account_id),
        )
        ids = set()
        for r in self.cursor.fetchall():
            other = r["linked_account_id"] if r["account_id"] == account_id else r["account_id"]
            if other != account_id:
                ids.add(other)
        # Не показываем связи с аккаунтами, которые лежат в корзине.
        result = [{"id": i, "name": self.get_account_path(i)}
                  for i in ids if not self._is_in_bin(i)]
        result.sort(key=lambda r: r["name"].lower())
        return result

    def set_links(self, account_id: int, target_ids: list[int]) -> None:
        """Задаёт связи аккаунта (симметрично). Старые связи этого аккаунта заменяются."""
        with self.conn:
            self._set_links_rows(account_id, target_ids)
        self._mark_dirty()

    def _set_links_rows(self, account_id: int, target_ids: list[int]) -> None:
        """Тело set_links без управления транзакцией/пометкой dirty — чтобы запись
        связей можно было выполнить в общей транзакции с save_account (H6-02)."""
        self.cursor.execute(
            "DELETE FROM linked_accounts WHERE account_id = ? OR linked_account_id = ?",
            (account_id, account_id),
        )
        seen = set()
        for t in target_ids:
            # Пропускаем самоссылку и повторы во входном списке (дедуп).
            if t == account_id or t in seen:
                continue
            seen.add(t)
            # Каноническая форма: account_id < linked_account_id. Так (A,B) и
            # (B,A) — одна и та же строка (закреплено CHECK в схеме, M3-06).
            lo, hi = canonical_link_pair(account_id, t)
            self.cursor.execute(
                "INSERT OR IGNORE INTO linked_accounts "
                "(account_id, linked_account_id) VALUES (?, ?)",
                (lo, hi),
            )

    # ----- Загрузка/сохранение полной карточки аккаунта -----
    # Работает с примитивами (str/int/bytes); конвертация дат и Qt-типов
    # выполняется в models.AccountData (to_storage/from_storage).

    def load_account(self, account_id):
        """Возвращает полную карточку аккаунта в виде словаря примитивов,
        или None, если аккаунт не найден."""
        self.cursor.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        row = self.cursor.fetchone()
        if not row:
            return None

        fields = {k: row[k] for k in (
            "account_name", "url", "login", "password", "creation_date",
            "password_changed_date", "password_change_interval_days",
            "notes", "ip", "browser", "os", "extra_info",
        )}

        self.cursor.execute("SELECT * FROM personal_data WHERE account_id = ?", (account_id,))
        prow = self.cursor.fetchone()
        personal = {
            k: (prow[k] if prow else None)
            for k in ("first_name", "last_name", "middle_name", "birth_date", "address")
        }

        self.cursor.execute(
            "SELECT question, answer FROM secret_questions WHERE account_id = ? ORDER BY id",
            (account_id,),
        )
        questions = [{"q": r["question"], "a": r["answer"]} for r in self.cursor.fetchall()]

        self.cursor.execute(
            "SELECT phrase, device_id FROM recovery_phrases WHERE account_id = ? ORDER BY id LIMIT 1",
            (account_id,),
        )
        rrow = self.cursor.fetchone()
        recovery = {
            "phrase": rrow["phrase"] if rrow else "",
            "device_id": rrow["device_id"] if rrow else "",
        }

        self.cursor.execute(
            "SELECT code FROM recovery_codes WHERE account_id = ? ORDER BY id", (account_id,)
        )
        codes = [r["code"] for r in self.cursor.fetchall()]

        self.cursor.execute(
            "SELECT id, description FROM gallery WHERE account_id = ? ORDER BY id",
            (account_id,),
        )
        gallery = [
            {"id": r["id"], "desc": r["description"] or "", "data": None}
            for r in self.cursor.fetchall()
        ]

        return {
            "fields": fields,
            "personal": personal,
            "questions": questions,
            "recovery": recovery,
            "codes": codes,
            "gallery": gallery,
        }

    def load_gallery_image(self, image_id: int):
        """Загружает BLOB одного изображения галереи по его id.
        Используется для ленивой загрузки: при load_account image_data не читается,
        а запрашивается отдельно только когда виджет хочет показать миниатюру."""
        self.cursor.execute("SELECT image_data FROM gallery WHERE id = ?", (image_id,))
        row = self.cursor.fetchone()
        if row is None or row["image_data"] is None:
            return None
        return bytes(row["image_data"])

    def vacuum(self):
        """VACUUM — дефрагментация и физическое сжатие файла БД (освобождает
        страницы, оставшиеся в freelist после удаления крупных BLOB).

        Должна выполняться ВНЕ транзакции — поэтому сначала фиксируем возможную
        открытую неявную транзакцию. В шифрованном режиме VACUUM меняет образ
        in-memory БД, поэтому помечаем её грязной для последующего persist()."""
        self.conn.commit()           # VACUUM не выполняется внутри транзакции
        self.conn.execute("VACUUM")
        if self.encrypted:
            self._mark_dirty()

    def save_account(self, account_id, data):
        """Сохраняет полную карточку аккаунта. data — словарь примитивов в
        формате load_account(). Связанные таблицы перезаписываются целиком."""
        with self.conn:
            self._save_account_rows(account_id, data)
        self._mark_dirty()

    def save_account_with_links(self, account_id, data, target_ids):
        """Атомарно сохраняет карточку и её связи В ОДНОЙ транзакции (H6-02):
        раньше save_account и set_links были двумя транзакциями — сбой второй
        оставлял карточку записанной, а связи нет. Теперь либо обе, либо ни одна."""
        with self.conn:
            self._save_account_rows(account_id, data)
            self._set_links_rows(account_id, target_ids)
        self._mark_dirty()

    def _save_account_rows(self, account_id, data):
        """Тело save_account без управления транзакцией/пометкой dirty (для
        переиспользования в save_account_with_links под общей транзакцией)."""
        f = data["fields"]
        self.cursor.execute(
            """UPDATE accounts SET
                account_name = ?, url = ?, login = ?, password = ?, creation_date = ?,
                password_changed_date = ?, password_change_interval_days = ?,
                notes = ?, ip = ?, browser = ?, os = ?, extra_info = ?
               WHERE id = ?""",
            (f["account_name"], f.get("url"), f["login"], f["password"], f["creation_date"],
             f["password_changed_date"], f["password_change_interval_days"],
             f["notes"], f["ip"], f["browser"], f["os"], f["extra_info"], account_id),
        )

        p = data["personal"]
        self.cursor.execute("DELETE FROM personal_data WHERE account_id = ?", (account_id,))
        self.cursor.execute(
            """INSERT INTO personal_data
               (account_id, first_name, last_name, middle_name, birth_date, address)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (account_id, p["first_name"], p["last_name"], p["middle_name"],
             p["birth_date"], p["address"]),
        )

        self.cursor.execute("DELETE FROM secret_questions WHERE account_id = ?", (account_id,))
        for q in data["questions"]:
            self.cursor.execute(
                "INSERT INTO secret_questions (account_id, question, answer) VALUES (?, ?, ?)",
                (account_id, q["q"], q["a"]),
            )

        r = data["recovery"]
        self.cursor.execute("DELETE FROM recovery_phrases WHERE account_id = ?", (account_id,))
        self.cursor.execute(
            "INSERT INTO recovery_phrases (account_id, phrase, device_id) VALUES (?, ?, ?)",
            (account_id, r["phrase"], r["device_id"]),
        )

        self.cursor.execute("DELETE FROM recovery_codes WHERE account_id = ?", (account_id,))
        for code in data["codes"]:
            self.cursor.execute(
                "INSERT INTO recovery_codes (account_id, code) VALUES (?, ?)",
                (account_id, code),
            )

        self.cursor.execute("DELETE FROM gallery WHERE account_id = ?", (account_id,))
        for g in data["gallery"]:
            blob = sqlite3.Binary(g["data"]) if g["data"] is not None else None
            self.cursor.execute(
                "INSERT INTO gallery (account_id, description, image_data) VALUES (?, ?, ?)",
                (account_id, g["desc"], blob),
            )

    # ----- Сбор данных для экспорта -----

    def export_subtree(self, node_type=None, node_id=None):
        """Собирает дерево с полными карточками аккаунтов для экспорта.

        node_type=None — вся база. Иначе возвращается только ветка указанного
        узла (папка/сервис/аккаунт). У каждого узла type=='account' добавлены
        ключи 'card' (как load_account) и 'links' (как get_links). Метод только
        читает БД (не помечает её грязной)."""
        full = self.get_tree_structure()  # ручной порядок, без корзины
        if node_type is None:
            roots = full
        else:
            found = self._find_node(full, node_type, node_id)
            roots = [found] if found else []

        # Bulk-предзагрузка вместо load_account()/get_links() на каждый аккаунт
        # (устранение N+1: раньше экспорт 100 аккаунтов делал ~728 SELECT).
        ids: list[int] = []
        for root in roots:
            self._collect_account_ids(root, ids)
        cards = self._load_cards_bulk(ids)
        links = self._load_links_bulk(ids)
        for root in roots:
            self._attach_cards_preloaded(root, cards, links)
        return roots

    def _find_node(self, nodes, node_type, node_id):
        """Рекурсивный поиск узла по (type, id) в структуре дерева."""
        for n in nodes:
            if n["type"] == node_type and n["id"] == node_id:
                return n
            child = self._find_node(n.get("children", []), node_type, node_id)
            if child:
                return child
        return None

    def _collect_account_ids(self, node, out):
        """Собирает id всех аккаунтов в ветке (рекурсивно)."""
        if node["type"] == "account":
            out.append(node["id"])
        for child in node.get("children", []):
            self._collect_account_ids(child, out)

    def _attach_cards_preloaded(self, node, cards, links):
        """Вкладывает предзагруженные карточку и связи в узлы-аккаунты."""
        if node["type"] == "account":
            node["card"] = cards.get(node["id"])
            node["links"] = links.get(node["id"], [])
        for child in node.get("children", []):
            self._attach_cards_preloaded(child, cards, links)

    @staticmethod
    def _chunks(seq, size=900):
        """Режет список на куски (предел числа параметров в SQLite ~999)."""
        for i in range(0, len(seq), size):
            yield seq[i:i + size]

    def _load_cards_bulk(self, account_ids):
        """{account_id: card} для набора аккаунтов. Card как в load_account()."""
        cards: dict[int, dict[str, Any]] = {}
        if not account_ids:
            return cards
        field_keys = ("account_name", "url", "login", "password", "creation_date",
                      "password_changed_date", "password_change_interval_days",
                      "notes", "ip", "browser", "os", "extra_info")
        personal_keys = ("first_name", "last_name", "middle_name", "birth_date", "address")

        for chunk in self._chunks(account_ids):
            ph = ",".join("?" * len(chunk))
            self.cursor.execute(f"SELECT * FROM accounts WHERE id IN ({ph})", chunk)
            for r in self.cursor.fetchall():
                cards[r["id"]] = {
                    "fields": {k: r[k] for k in field_keys},
                    "personal": {k: None for k in personal_keys},
                    "questions": [],
                    "recovery": {"phrase": "", "device_id": ""},
                    "codes": [],
                    "gallery": [],
                }

        for chunk in self._chunks(account_ids):
            ph = ",".join("?" * len(chunk))
            # personal_data: берём первую строку на аккаунт (как fetchone в load_account)
            self.cursor.execute(
                f"SELECT * FROM personal_data WHERE account_id IN ({ph}) ORDER BY account_id, id", chunk)
            seen_personal = set()
            for r in self.cursor.fetchall():
                aid = r["account_id"]
                if aid in cards and aid not in seen_personal:
                    cards[aid]["personal"] = {k: r[k] for k in personal_keys}
                    seen_personal.add(aid)

            self.cursor.execute(
                f"SELECT account_id, question, answer FROM secret_questions "
                f"WHERE account_id IN ({ph}) ORDER BY account_id, id", chunk)
            for r in self.cursor.fetchall():
                c = cards.get(r["account_id"])
                if c is not None:
                    c["questions"].append({"q": r["question"], "a": r["answer"]})

            # recovery_phrases: первая на аккаунт (как LIMIT 1 в load_account)
            self.cursor.execute(
                f"SELECT account_id, phrase, device_id FROM recovery_phrases "
                f"WHERE account_id IN ({ph}) ORDER BY account_id, id", chunk)
            seen_rec = set()
            for r in self.cursor.fetchall():
                aid = r["account_id"]
                if aid in cards and aid not in seen_rec:
                    cards[aid]["recovery"] = {"phrase": r["phrase"] or "",
                                              "device_id": r["device_id"] or ""}
                    seen_rec.add(aid)

            self.cursor.execute(
                f"SELECT account_id, code FROM recovery_codes "
                f"WHERE account_id IN ({ph}) ORDER BY account_id, id", chunk)
            for r in self.cursor.fetchall():
                c = cards.get(r["account_id"])
                if c is not None:
                    c["codes"].append(r["code"])

            self.cursor.execute(
                f"SELECT account_id, description, image_data FROM gallery "
                f"WHERE account_id IN ({ph}) ORDER BY account_id, id", chunk)
            for r in self.cursor.fetchall():
                c = cards.get(r["account_id"])
                if c is not None:
                    c["gallery"].append({
                        "desc": r["description"] or "",
                        "data": bytes(r["image_data"]) if r["image_data"] is not None else None,
                    })

        return cards

    def _name_maps(self):
        """Карты имён для построения путей без запроса на каждый аккаунт."""
        self.cursor.execute("SELECT id, account_name, service_id, deleted_at FROM accounts")
        acc = {r["id"]: (r["account_name"], r["service_id"], r["deleted_at"])
               for r in self.cursor.fetchall()}
        self.cursor.execute("SELECT id, name, folder_id FROM services")
        svc = {r["id"]: (r["name"], r["folder_id"]) for r in self.cursor.fetchall()}
        self.cursor.execute("SELECT id, name FROM folders")
        fld = {r["id"]: r["name"] for r in self.cursor.fetchall()}
        return acc, svc, fld

    @staticmethod
    def _path_from_maps(account_id, acc, svc, fld):
        a = acc.get(account_id)
        if not a:
            return ""
        name, service_id, _deleted = a
        parts = []
        if service_id and service_id in svc:
            sname, folder_id = svc[service_id]
            if folder_id and folder_id in fld:
                parts.append(fld[folder_id])
            parts.append(sname)
        parts.append(name)
        return " / ".join(parts)

    def _load_links_bulk(self, account_ids):
        """{account_id: [{"id","name"(путь)}]} — связи в обе стороны, без
        аккаунтов из корзины, отсортированные по пути (как get_links)."""
        result: dict[int, list[dict[str, Any]]] = {i: [] for i in account_ids}
        if not account_ids:
            return result
        acc, svc, fld = self._name_maps()
        others: dict[int, set[int]] = {i: set() for i in account_ids}
        for chunk in self._chunks(account_ids):
            ph = ",".join("?" * len(chunk))
            self.cursor.execute(
                f"SELECT account_id, linked_account_id FROM linked_accounts "
                f"WHERE account_id IN ({ph}) OR linked_account_id IN ({ph})",
                chunk + chunk)
            for r in self.cursor.fetchall():
                a, b = r["account_id"], r["linked_account_id"]
                if a in others and b != a:
                    others[a].add(b)
                if b in others and a != b:
                    others[b].add(a)
        for i, oset in others.items():
            rows = []
            for o in oset:
                a = acc.get(o)
                if not a or a[2]:        # нет записи или аккаунт в корзине
                    continue
                rows.append({"id": o, "name": self._path_from_maps(o, acc, svc, fld)})
            rows.sort(key=lambda r: r["name"].lower())
            result[i] = rows
        return result


# ─── Авто-сериализация доступа к соединению (потокобезопасность) ──────────────
# Соединение открывается с check_same_thread=False, чтобы run_async() мог работать
# с ним из фонового потока. Чтобы синхронные вызовы из UI-потока и async-вызовы из
# воркера никогда не трогали conn/cursor одновременно, КАЖДЫЙ публичный метод
# оборачивается в self._lock (RLock — реентрантный: вложенные вызовы между методами
# на одном потоке не дают дедлок). Делается единым проходом по классу, чтобы не
# засорять декоратором каждое определение и не забыть новый метод.
#
# Исключения (_DB_NO_LOCK) — методы, чья тяжёлая часть это шифрование/файловый I/O,
# а НЕ доступ к conn. Держать на них лок означало бы блокировать все чтения на всё
# время записи контейнера. Их conn-часть (serialize_db, conn.close) залочена сама.
_DB_NO_LOCK = frozenset({
    "serialize_container",   # вызывает serialize_db (залочен) + AES-GCM (тяжёлый, без лока)
    "persist",               # serialize_container + запись файла
    "seal_and_write",        # без conn; выполняется в QThread писателя — лок недопустим
    "flush",                 # обёртка над persist
    "run_async",             # сам диспетчер async (корутина); лок берёт вызываемый метод
    "shutdown_executor",     # управление пулом, не трогает conn
})


def _synchronized(method):
    @functools.wraps(method)
    def _wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return _wrapper


for _nm, _fn in list(vars(Database).items()):
    if (isinstance(_fn, type(_synchronized))          # обычная функция-метод (не static/classmethod)
            and not _nm.startswith("_")
            and _nm not in _DB_NO_LOCK):
        setattr(Database, _nm, _synchronized(_fn))