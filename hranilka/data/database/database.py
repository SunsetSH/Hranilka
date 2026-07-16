"""Database — сборка класса из mixin-модулей + обслуживание БД.

Разрез по ответственностям:
  errors/         — доменные исключения (пакет, файл на класс);
  state.py        — DbBase: аннотации и заглушки кросс-модульных методов;
  concurrency.py  — RLock + executor + поколения сессий (async-доступ);
  persistence.py  — файл/контейнер AES-GCM, отложенная запись, шифрование;
  schema.py       — DDL, версия схемы, pre-migrate-копии;
  migrations/     — конвейер и реестр нумерованных миграций;
  tree_ops.py     — дерево: папки/сервисы/узлы, сортировка, корзина;
  accounts.py     — карточка аккаунта и связи;
  gallery_ops.py  — галерея (BLOB, кэш объёма);
  bulk.py         — экспорт поддерева и bulk-выгрузки;
  здесь           — __init__, wipe_all_data/vacuum и авто-обёртка публичных
                    методов в лок (см. конец файла).

Имена SCHEMA_VERSION и исключений реэкспортируются отсюда — внешний код
по-прежнему импортирует их из этого модуля."""
import functools
import inspect
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from hranilka.data.database.accounts import DbAccountCardMixin
from hranilka.data.database.bulk import DbBulkOpsMixin
from hranilka.data.database.concurrency import DbConcurrencyMixin
from hranilka.data.database.fin_items import DbFinItemsMixin
from hranilka.data.database.gallery_ops import DbGalleryOpsMixin
from hranilka.data.database.persistence import DbPersistenceMixin
from hranilka.data.database.schema import (SCHEMA_VERSION, _REQUIRED_TABLES,
                                           DbSchemaMixin)
from hranilka.data.database.servers import DbServersMixin
from hranilka.data.database.tree_ops import DbTreeOpsMixin
from hranilka.data.errors import (CorruptedPayloadError, FutureSchemaError,
                                  PreMigrationBackupError, StaleSessionError,
                                  VaultConflictError)

__all__ = ["Database", "SCHEMA_VERSION", "CorruptedPayloadError",
           "FutureSchemaError", "PreMigrationBackupError",
           "StaleSessionError", "VaultConflictError"]


class Database(DbConcurrencyMixin, DbPersistenceMixin, DbSchemaMixin,
               DbTreeOpsMixin, DbAccountCardMixin, DbGalleryOpsMixin,
               DbFinItemsMixin, DbServersMixin, DbBulkOpsMixin):
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
        # Кэш суммарного объёма галереи (M-9): SUM(LENGTH(image_data)) — полный
        # скан gallery, а он вызывается при каждом добавлении картинки. Держим
        # мемо-значение (None = не посчитано/устарело), инвалидируем при любой
        # записи в галерею и смене сессии; recompute лениво по запросу.
        self._gallery_bytes: int | None = None


    def wipe_all_data(self):
        """Удаляет все пользовательские данные, сохраняя структуру таблиц.

        Удаляем от дочерних таблиц к родительским, поэтому внешние ключи можно
        не отключать. Раньше код вызывал `PRAGMA foreign_keys = OFF/ON`, но
        PRAGMA внутри неявной транзакции (её открывают DELETE) игнорируется, и
        после очистки FK оставались ВЫКЛЮЧЕННЫМИ до перезапуска — все
        последующие ON DELETE CASCADE/SET NULL переставали работать."""
        with self.conn:
            # Финансовые записи и серверы не являются дочерними accounts:
            # свободные карты/кошельки/серверы пережили бы очистку, если не
            # удалить их явно. Удаляем дочерние таблицы раньше владельцев и
            # не отключаем FK.
            for table in ("fin_gallery", "fin_links", "fin_items",
                           "server_gallery", "server_links", "servers", "gallery",
                           "recovery_codes", "recovery_phrases",
                           "secret_questions", "personal_data", "linked_accounts",
                           "accounts", "services", "folders"):
                self.conn.execute(f"DELETE FROM {table}")
        self._invalidate_gallery_bytes()   # галерея очищена (M-9)
        self._mark_dirty()

    def invalidate_async_session(self):
        """Отменить поставленные в очередь операции текущей сессии БД.

        Нужен перед разрушительными действиями над содержимым (полная очистка):
        queued ``run_async`` с прежним токеном завершится StaleSessionError, а
        уже выполняющаяся операция закончится до захвата общего RLock.
        Соединение при этом не закрывается.
        """
        self._bump_session()
            

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
    "current_session",       # чтение токена сессии (атомарно), лок не нужен
    "shutdown_executor",     # управление пулом, не трогает conn
    "wait_executor_idle",    # барьер пула: держать лок нельзя (иначе дедлок с
                             # in-flight-методом, берущим тот же лок из воркера)
})

# ─── ИНВАРИАНТ ЛОКА (H-5) ─────────────────────────────────────────────────────
# serialize_db ОБЯЗАН оставаться обёрнутым локом: это единственная точка, где
# из фонового потока читается conn (conn.serialize). Методы без лока —
# flush / persist / seal_and_write / serialize_container — выполняются в потоке
# писателя и полагаются на то, что их conn-часть (именно serialize_db) берёт лок
# сама. Если serialize_db попадёт в _DB_NO_LOCK, сериализация БД пойдёт без
# лока параллельно UI-записи в conn → гонка/порча образа БД.
assert "serialize_db" not in _DB_NO_LOCK, (
    "serialize_db должен оставаться под локом (см. инвариант лока H-5)")


def _synchronized(method):
    @functools.wraps(method)
    def _wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return _wrapper

# Проход по dir(), а не vars(Database): методы разнесены по mixin-модулям
# (этап 2), vars() видит только тело самого класса — унаследованные публичные
# методы остались бы БЕЗ лока. inspect.getattr_static возвращает несвязанную
# функцию из нужного класса MRO (заглушки DbBase перекрыты реализациями).
for _nm in dir(Database):
    if _nm.startswith("_") or _nm in _DB_NO_LOCK:
        continue
    _fn = inspect.getattr_static(Database, _nm)
    if isinstance(_fn, type(_synchronized)):   # обычная функция (не static/classmethod)
        setattr(Database, _nm, _synchronized(_fn))
