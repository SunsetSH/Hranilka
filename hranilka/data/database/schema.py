"""Схема БД: DDL всех таблиц, версия схемы, декларативная миграция старых баз
и durable-копии перед необратимыми правками. Часть класса Database
(database.py) — методы вынесены дословно, этап 2 реструктуризации."""
import logging
import os
from datetime import datetime

from hranilka.data.errors import FutureSchemaError, PreMigrationBackupError
from hranilka.data.database.state import DbBase
from hranilka.core.util import best_effort_wipe

# Версия схемы базы данных. Увеличивается при изменении структуры таблиц,
# чтобы _migrate() мог обновить существующие документы пользователей.
# v6: единоразовый прогон полной нормализации (дедуп + UNIQUE-индексы) для
#     старых баз и переход на быстрый старт (см. create_tables: если версия
#     актуальна — миграция/дедуп пропускаются).
# v7: канонизация связей — хранить только пары (min,max) и закрепить инвариант
#     CHECK(account_id < linked_account_id) пересборкой таблицы linked_accounts.
# v8: обкатка конвейера нумерованных миграций (LEGACY_BASE) — реестр пуст.
# v9: финансовые сущности (карты/кошельки) — таблицы fin_items/fin_links/
#     fin_gallery + первая боевая нумерованная миграция m009 (аддитивная).
# v10: удаление типа «Банковский счёт» (bank_account) — миграция m010 чистит
#      его записи из fin_items (FK CASCADE подчищает fin_links/fin_gallery).
# v11: удаление типа «Электронный кошелёк» (ewallet) — миграция m011 чистит
#      его записи из fin_items (FK CASCADE подчищает fin_links/fin_gallery).
SCHEMA_VERSION = 11

# Обязательные таблицы актуальной схемы. На «быстром пути» create_tables() даже
# при совпадении версии проверяет их наличие (M6-06): частично повреждённую базу
# нельзя принимать слепо — недостающие таблицы будут пересозданы.
_REQUIRED_TABLES = frozenset({
    "folders", "services", "accounts", "personal_data", "secret_questions",
    "recovery_phrases", "recovery_codes", "gallery", "linked_accounts", "app_meta",
    "fin_items", "fin_links", "fin_gallery",
})


class DbSchemaMixin(DbBase):
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
        if (_ver == SCHEMA_VERSION and _REQUIRED_TABLES.issubset(existing)
                and self._fast_path_fingerprint_ok()):
            return              # схема актуальна и отпечаток цел — делать нечего
        # M6-06/M65-04: даже при «актуальной» версии не доверяем ей слепо. Помимо
        # наличия таблиц сверяем отпечаток схемы (обязательные колонки accounts и
        # UNIQUE-индексы целостности). Если что-то не сходится (частично повреждённая
        # база) — НЕ возвращаемся рано, а проходим ниже CREATE TABLE IF NOT EXISTS,
        # dedup и пересоздание индексов и восстанавливаем схему. Полный
        # foreign_key_check на горячем пути не запускаем (дорого на больших базах);
        # он выполняется при restore (см. backup._is_valid_db).

        # M65-05: durable-копия файла БД ПЕРЕД миграцией версии. Удаляется при
        # успехе (в конце метода); если миграция прервётся исключением/сбоем —
        # копия останется на диске (путь в логе) для восстановления.
        premigrate = self._begin_premigration_backup(_ver)

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
                mobile_phone TEXT,
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

        # Финансовые таблицы v9 (карты/кошельки). Единый источник SQL с миграцией
        # m009 — оба пути (новая база здесь, база v8 в m009) создают одинаковую
        # схему. Идемпотентно (IF NOT EXISTS): индексы создаются тут же.
        self._create_fin_tables(self.cursor)

        # Канонизация существующей таблицы связей (v6→v7): нормализация пар к
        # (min,max), дедуп и добавление CHECK. Выполняется ДО создания индексов
        # ниже, чтобы uq_linked_pair лёг уже на пересобранную таблицу.
        self._rebuild_linked_accounts_if_needed()

        # Деструктивный dedup (удаление дублирующих строк) может выполниться и на
        # базе АКТУАЛЬНОЙ версии — например, когда версия совпала, но отпечаток
        # схемы не сошёлся (частично повреждённая база). Тогда миграционной копии
        # (_begin_premigration_backup выше) ещё нет, а удаление строк необратимо.
        # Создаём durable-копию и здесь (H7-03c), если её ещё не сделали. Быстрый
        # путь (отпечаток цел) сюда не доходит — стартовые копии не плодятся.
        premigrate_dedup = None
        if premigrate is None:
            premigrate_dedup = self._make_durable_copy(
                _ver, "восстановление/нормализация схемы (dedup)")

        # Недостающие колонки старых баз — ДО создания индексов ниже: индекс
        # idx_accounts_deleted ссылается на accounts(deleted_at), и на базе без
        # этой колонки CREATE INDEX падал (латентный баг: diff колонок раньше
        # выполнялся только в _migrate(), уже ПОСЛЕ индексов).
        self._add_missing_columns()

        # Дедуп + (пере)создание индексов — ОДНОЙ транзакцией (M65-05): при сбое
        # посередине (диск/исключение) `with self.conn` откатит и удаление дублей,
        # и создание индексов целиком, не оставив промежуточного состояния (дубли
        # удалены, а UNIQUE-индекс ещё не создан). Успех фиксируется атомарно.
        with self.conn:
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
        self._mark_dirty()   # `with self.conn` уже зафиксировал; отметим для encrypted flush

        # Доработка существующих баз до актуальной схемы
        self._migrate()
        # Правка схемы завершена успешно — durable-копии этого прогона больше не
        # нужны (удаляем только их, уцелевшие копии прошлых прогонов не трогаем).
        self._finish_premigration_backup(premigrate)
        self._finish_premigration_backup(premigrate_dedup)

    def _add_missing_columns(self):
        """Добавить в старые базы недостающие колонки из _EXPECTED_COLUMNS
        (ALTER TABLE ADD COLUMN идемпотентен в рамках проверки). Вызывается из
        create_tables (до индексов) и из migrations.runner (legacy-diff)."""
        for table, columns in self._EXPECTED_COLUMNS.items():
            existing = self._get_columns(table)
            for name, definition in columns.items():
                if name not in existing:
                    self.cursor.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    )

    # Ожидаемые колонки таблиц для добавления в старые базы (имя -> SQL-определение).
    # Используется в _add_missing_columns; новые базы создаются полными в create_tables().
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
        "personal_data": {
            "mobile_phone": "TEXT",
        },
    }

    def _get_columns(self, table):
        """Возвращает множество имён колонок таблицы."""
        self.cursor.execute(f"PRAGMA table_info({table})")
        return {row["name"] for row in self.cursor.fetchall()}

    # UNIQUE-индексы, задающие целостность актуальной схемы (одна ПД/фраза на
    # аккаунт, отсутствие дублей связей). Их наличие — часть отпечатка «быстрого
    # пути»: удалённый вручную UNIQUE-индекс раньше проходил незамеченным (M65-04).
    _INTEGRITY_INDEXES = frozenset({
        "uq_personal_account", "uq_phrases_account", "uq_linked_pair",
        "uq_fin_link",
    })

    def _fast_path_fingerprint_ok(self):
        """Лёгкая проверка отпечатка схемы на горячем пути (M65-04).

        Помимо совпадения версии и наличия таблиц убеждаемся, что на месте
        обязательные колонки accounts и UNIQUE-индексы целостности. Так частично
        повреждённая (но с актуальной версией) база не будет принята слепо: при
        несовпадении отпечатка вызыватель пройдёт путь восстановления схемы.
        Дёшево — несколько PRAGMA/чтений sqlite_master, без сканирования данных."""
        for table, columns in self._EXPECTED_COLUMNS.items():
            if not columns.keys() <= self._get_columns(table):
                return False
        self.cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row["name"] for row in self.cursor.fetchall()}
        return self._INTEGRITY_INDEXES <= indexes

    def _warn_leftover_premigrate(self):
        """Найти уцелевшие .pre-migrate*-копии от ПРЕДЫДУЩИХ неудачных прогонов
        и заметно залогировать их пути (H7-03b). Такие файлы — рабочие снимки
        базы «до миграции»: если предыдущий запуск прервался, они остались на
        диске. НЕ удаляем их (могут понадобиться для ручного восстановления),
        но громко предупреждаем — иначе пользователь о них не узнает."""
        directory = os.path.dirname(self.db_path) or "."
        base = os.path.basename(self.db_path) + ".pre-migrate"
        try:
            names = os.listdir(directory)
        except OSError:
            return
        for name in names:
            if name.startswith(base):
                logging.error(
                    "Обнаружена уцелевшая резервная копия от прошлой прерванной "
                    "миграции: %s. Файл НЕ удалён — используйте его для "
                    "восстановления при необходимости.",
                    os.path.join(directory, name))

    def _make_durable_copy(self, ver, reason):
        """Durable-копия файла БД ПЕРЕД необратимой правкой схемы (H7-03).

        Используется и перед миграцией версии, и перед деструктивным dedup +
        пересборкой UNIQUE-индексов (M7-01). Только plaintext: в шифрованном
        режиме на диске уже лежит НЕИЗМЕНЁННЫЙ контейнер прежней версии — он и
        есть снимок «до правки» (persist происходит уже после).

        Имя копии УНИКАЛЬНО (`<db>.pre-migrate-v{ver}-{YYYYMMDD-HHMMSS}`): копию
        от прошлого прерванного прогона мы НЕ перезаписываем (иначе рабочий файл
        затёр бы уцелевший снимок — H7-03b). Возвращает путь копии (или None,
        если копия не нужна: encrypted / нет файла на диске).

        При сбое копирования поднимает PreMigrationBackupError — правка схемы
        НЕ должна продолжаться без резервной копии (H7-03a)."""
        if self.encrypted:
            return None
        if not os.path.exists(self.db_path):
            return None
        # Уцелевшие копии прошлых прерванных прогонов — заметно в лог, не трогаем.
        self._warn_leftover_premigrate()
        ver_tag = "unknown" if ver is None else str(ver)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = f"{self.db_path}.pre-migrate-v{ver_tag}-{stamp}"
        # Имя уникально по метке времени; на случай двух прогонов в одну секунду
        # добавляем счётчик — «xb» не перезапишет уцелевшую копию прошлого прогона.
        path = base
        try:
            # Файл на диске согласован (транзакция ещё не начиналась) — копируем и
            # принудительно сбрасываем на диск (устойчивость к сбою питания).
            # "xb": НИКОГДА не перезаписываем существующий файл (H7-03b).
            dst = None
            for suffix in ("", "-1", "-2", "-3", "-4", "-5"):
                path = base + suffix
                try:
                    dst = open(path, "xb")
                    break
                except FileExistsError:
                    continue
            if dst is None:
                raise OSError("не удалось подобрать уникальное имя копии")
            with open(self.db_path, "rb") as src, dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
        except OSError as e:
            logging.error("Не удалось создать резервную копию перед %s: %s",
                          reason, e)
            # Частично записанный файл (если успел появиться) — убрать.
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            raise PreMigrationBackupError(path, e)
        logging.info("%s: сохранена резервная копия: %s", reason, path)
        return path

    def _begin_premigration_backup(self, ver):
        """Durable-копия ПЕРЕД миграцией версии (только при ver < текущей)."""
        if ver is None or ver >= SCHEMA_VERSION:
            return None
        return self._make_durable_copy(
            ver, f"миграция схемы {ver}→{SCHEMA_VERSION}")

    def _finish_premigration_backup(self, path):
        """Удалить durable-копию, созданную ЭТИМ прогоном, после успеха (H7-03).

        Копия — полный plaintext-снимок БД (все пароли/BLOB), поэтому удаляем
        через best_effort_wipe: содержимое затирается нулями до unlink (H-3).
        Удаляется ТОЛЬКО копия этого прогона — уцелевшие копии прошлых
        прерванных прогонов не трогаем (см. _warn_leftover_premigrate)."""
        if not path:
            return
        best_effort_wipe(path)

    def _migrate(self):
        """Приводит существующую базу к актуальной версии схемы.

        Тонкая обёртка над migrations.runner (этап 3): legacy-diff колонок +
        нумерованные шаги из реестра MIGRATIONS. Имя и сигнатура сохранены —
        тесты патчат метод на экземпляре. Импорт отложенный: runner импортирует
        SCHEMA_VERSION из этого модуля (разрыв цикла schema↔runner)."""
        from hranilka.data.database.migrations import runner
        runner.run(self)

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

    # ----- Финансовые таблицы v9 (карты/кошельки) -----
    # DDL — дословно из концепта fin-entities-concept.md §2. Единый источник для
    # create_tables() (новые базы) и миграции m009 (базы v8): расхождение двух
    # путей создания исключено по построению.

    @staticmethod
    def _fin_items_create_sql(table_name="fin_items", if_not_exists=False):
        """CREATE TABLE для fin_items — финансовые записи (карты/кошельки).

        service_id допускает NULL («свободный» элемент, как у accounts). data —
        JSON-полезная нагрузка типа ({"v":1,...}). card_last4/expires_on —
        экстракт-колонки для горячих путей (поиск/предупреждения), заполняются
        CRUD-слоем при каждом save из data (единственный писатель)."""
        ine = "IF NOT EXISTS " if if_not_exists else ""
        return f"""
            CREATE TABLE {ine}{table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id INTEGER,
                item_type TEXT NOT NULL,
                name TEXT NOT NULL,
                data TEXT NOT NULL DEFAULT '{{}}',
                card_last4 TEXT,
                expires_on DATE,
                is_favorite INTEGER DEFAULT 0,
                sort_order INTEGER DEFAULT 0,
                deleted_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE CASCADE
            )
        """

    @staticmethod
    def _fin_links_create_sql(table_name="fin_links", if_not_exists=False):
        """CREATE TABLE для fin_links — связь элемент↔аккаунт.

        Связь разнотипна (роли фиксированы колонками), поэтому канонизация не
        нужна — достаточно UNIQUE(item_id, account_id) (индекс uq_fin_link).
        Обе стороны каскадно чистятся при удалении элемента/аккаунта."""
        ine = "IF NOT EXISTS " if if_not_exists else ""
        return f"""
            CREATE TABLE {ine}{table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                FOREIGN KEY (item_id) REFERENCES fin_items(id) ON DELETE CASCADE,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """

    @staticmethod
    def _fin_gallery_create_sql(table_name="fin_gallery", if_not_exists=False):
        """CREATE TABLE для fin_gallery — фото карты/скан договора (зеркало gallery,
        FK на item_id). Ленивая загрузка BLOB — контракт H-6/M7-03."""
        ine = "IF NOT EXISTS " if if_not_exists else ""
        return f"""
            CREATE TABLE {ine}{table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                description TEXT,
                image_data BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (item_id) REFERENCES fin_items(id) ON DELETE CASCADE
            )
        """

    @staticmethod
    def _fin_index_sql():
        """Идемпотентные индексы финансовых таблиц (единый источник для
        create_tables и m009). uq_fin_link — часть отпечатка целостности."""
        return (
            "CREATE INDEX IF NOT EXISTS idx_fin_items_service ON fin_items(service_id)",
            "CREATE INDEX IF NOT EXISTS idx_fin_items_deleted ON fin_items(deleted_at)",
            "CREATE INDEX IF NOT EXISTS idx_fin_items_type ON fin_items(item_type)",
            "CREATE INDEX IF NOT EXISTS idx_fin_items_last4 ON fin_items(card_last4)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_fin_link ON fin_links(item_id, account_id)",
            "CREATE INDEX IF NOT EXISTS idx_fin_links_account ON fin_links(account_id)",
        )

    @staticmethod
    def _create_fin_tables(executor):
        """Создаёт финансовые таблицы и индексы (идемпотентно). executor —
        объект с .execute (self.cursor в create_tables либо conn в m009), чтобы
        оба пути создания использовали один и тот же DDL."""
        executor.execute(
            DbSchemaMixin._fin_items_create_sql(if_not_exists=True))
        executor.execute(
            DbSchemaMixin._fin_links_create_sql(if_not_exists=True))
        executor.execute(
            DbSchemaMixin._fin_gallery_create_sql(if_not_exists=True))
        for stmt in DbSchemaMixin._fin_index_sql():
            executor.execute(stmt)

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
        # Инвариант (M-7): PRAGMA foreign_keys переключается ТОЛЬКО вне открытой
        # транзакции — внутри транзакции SQLite молча игнорирует PRAGMA, и FK
        # остались бы включёнными во время пересборки таблицы.
        assert not self.conn.in_transaction
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
        # Инвариант (M-7): PRAGMA foreign_keys — только вне транзакции, иначе
        # SQLite молча игнорирует её и FK останутся включёнными при пересборке.
        assert not self.conn.in_transaction
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
        # Инвариант (M-7): PRAGMA foreign_keys — только вне транзакции, иначе
        # SQLite молча игнорирует её и FK останутся включёнными при пересборке.
        assert not self.conn.in_transaction
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
