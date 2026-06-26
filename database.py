import sqlite3
import json
import os
from datetime import datetime, date
from pathlib import Path


def _days_until_password_change(changed_date_str, interval_days):
    """Сколько дней осталось до смены пароля (может быть отрицательным, если
    срок уже прошёл). None — если срок не задан или дата некорректна."""
    if not changed_date_str or not interval_days:
        return None
    try:
        changed = datetime.strptime(str(changed_date_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    due = changed.toordinal() + int(interval_days)
    return due - date.today().toordinal()


# Версия схемы базы данных. Увеличивается при изменении структуры таблиц,
# чтобы _migrate() мог обновить существующие документы пользователей.
SCHEMA_VERSION = 5

class Database:
    def __init__(self, db_path="hranilka.db"):
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        # Состояние шифрования. Когда encrypted=True, БД живёт в sqlite :memory:,
        # а на диске лежит зашифрованный контейнер (см. crypto_store).
        self.encrypted = False
        self._dek = None         # ключ данных (расшифрованный), только в памяти
        self._header = None      # заголовок контейнера (соли, обёрнутые DEK)
        # Отложенная запись на диск (только шифр. режим): мутаторы помечают БД
        # «грязной», а контроллер сбрасывает её один раз за оборот событийного
        # цикла. _on_dirty — callback контроллера (или None), вызывается при
        # появлении несохранённых изменений в шифрованном режиме.
        self._dirty = False
        self._on_dirty = None

    def _setup_conn(self):
        """Общие настройки соединения (row_factory, внешние ключи)."""
        self.conn.row_factory = sqlite3.Row  # Чтобы обращаться к полям по имени
        # Без этого ON DELETE CASCADE/SET NULL не работают в SQLite.
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.cursor = self.conn.cursor()

    def connect(self):
        """Открыть обычную (незашифрованную) базу — файл на диске."""
        self.encrypted = False
        self._dek = None
        self._header = None
        self._dirty = False
        self.conn = sqlite3.connect(self.db_path)
        self._setup_conn()

    def open_encrypted(self, db_bytes: bytes, dek: bytes, header: dict):
        """Открыть расшифрованные байты БД в памяти. Файл остаётся шифрованным;
        изменения сбрасываются на диск через persist()."""
        self.encrypted = True
        self._dek = dek
        self._header = header
        self._dirty = False
        self.conn = sqlite3.connect(":memory:")
        if db_bytes:
            self.conn.deserialize(db_bytes)
        self._setup_conn()

    def persist(self):
        """Сбросить текущее состояние БД на диск.

        В обычном режиме — no-op (SQLite уже пишет в файл). В зашифрованном —
        сериализует in-memory БД, шифрует и атомарно записывает контейнер."""
        if not self.encrypted or self.conn is None or self._dek is None:
            return
        import crypto_store as cs
        db_bytes = self.conn.serialize()
        container = cs.seal(db_bytes, self._dek, self._header)
        tmp = self.db_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(container)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.db_path)

    def set_header(self, header: dict):
        """Обновить заголовок контейнера (после смены пароля/recovery) и сохранить."""
        self._header = header
        self.persist()

    def lock(self):
        """Заблокировать: сохранить, закрыть in-memory БД, забыть ключ."""
        if self.encrypted:
            try:
                self.persist()
            except Exception:
                pass
        if self.conn:
            self.conn.close()
        self.conn = None
        self.cursor = None
        self._dek = None
        self._dirty = False

    def _mark_dirty(self):
        """Пометить БД как изменённую и уведомить контроллер (для отложенной
        записи). В незашифрованном режиме запись не нужна — данные уже в файле."""
        self._dirty = True
        if self.encrypted and self._on_dirty is not None:
            self._on_dirty()

    def flush(self):
        """Сбросить накопленные изменения на диск (если есть). Вызывается
        контроллером по таймеру и в финальных точках."""
        if self._dirty:
            self.persist()
            self._dirty = False

    def _commit(self):
        """Зафиксировать транзакцию и пометить БД грязной (отложенная запись
        в шифрованном режиме; в обычном — no-op, файл уже на диске)."""
        self.conn.commit()
        self._mark_dirty()

    # ─── Управление шифрованием ──────────────────────────────────────────────

    def _atomic_write(self, data: bytes):
        tmp = self.db_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.db_path)

    def enable_encryption(self, password: str, preset: str):
        """Зашифровать текущую (обычную) БД. Возвращает recovery-код.
        После вызова БД работает в зашифрованном режиме (в памяти)."""
        import crypto_store as cs
        db_bytes = self.conn.serialize()
        container, recovery = cs.create_vault(db_bytes, password, preset)
        self.conn.close()
        self._atomic_write(container)
        # Переоткрыть как зашифрованную (получаем dek/header из контейнера).
        pt, dek, header = cs.unlock(container, password)
        self.open_encrypted(pt, dek, header)
        return recovery

    def disable_encryption(self):
        """Расшифровать БД обратно в обычный файл и работать без шифрования."""
        db_bytes = self.conn.serialize()
        self.conn.close()
        # Сериализованные байты — это валидный файл SQLite; пишем как есть.
        self._atomic_write(db_bytes)
        self.connect()

    def change_master_password(self, new_password: str, preset: str = None):
        """Сменить мастер-пароль (перезаворачивание DEK, без перешифровки данных)."""
        import crypto_store as cs
        new_header = cs.change_password(None, self._dek, self._header,
                                        new_password, preset)
        self.set_header(new_header)

    def regenerate_recovery_code(self):
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
        
        # Таблица связанных аккаунтов
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS linked_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                linked_account_id INTEGER NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                FOREIGN KEY (linked_account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        
        # Таблица служебных метаданных (версия схемы, и т.п.)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

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

    def close(self, persist=True):
        """Закрытие соединения. persist=False — закрыть БЕЗ сохранения на диск
        (нужно при восстановлении бэкапа, чтобы не затереть восстановленный файл
        текущей in-memory базой)."""
        if persist and self.encrypted and self.conn is not None and self._dek is not None:
            try:
                self.persist()
                self._dirty = False
            except Exception:
                pass
        if self.conn:
            self.conn.close()

    def wipe_all_data(self):
        """Удаляет все пользовательские данные, сохраняя структуру таблиц."""
        self.conn.execute("PRAGMA foreign_keys = OFF")
        for table in ("gallery", "recovery_codes", "recovery_phrases",
                       "secret_questions", "personal_data", "linked_accounts",
                       "accounts", "services", "folders"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._commit()
            
    def add_folder(self, name):
        """Добавление папки"""
        self.cursor.execute(
            "INSERT INTO folders (name) VALUES (?)",
            (name,)
        )
        self._commit()
        return self.cursor.lastrowid
    
    def add_service(self, name, folder_id=None):
        """Добавление сервиса"""
        self.cursor.execute(
            "INSERT INTO services (name, folder_id) VALUES (?, ?)",
            (name, folder_id)
        )
        self._commit()
        return self.cursor.lastrowid
    
    def add_account(self, service_id, account_name, login=None, password=None):
        """Добавление аккаунта"""
        self.cursor.execute(
            "INSERT INTO accounts (service_id, account_name, login, password, created_at) VALUES (?, ?, ?, ?, ?)",
            (service_id, account_name, login, password, datetime.now())
        )
        self._commit()
        return self.cursor.lastrowid
    
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
            'pwd_days_left': _days_until_password_change(
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
        Верхний уровень: папки, затем сервисы вне папок, затем свободные аккаунты."""
        result = []
        order = self._container_order(sort_mode, descending)

        # Папки
        self.cursor.execute(f"SELECT * FROM folders ORDER BY {order}")
        for folder in self.cursor.fetchall():
            folder_data = {'type': 'folder', 'id': folder['id'], 'name': folder['name'], 'children': []}
            self.cursor.execute(
                f"SELECT * FROM services WHERE folder_id = ? ORDER BY {order}", (folder['id'],)
            )
            for service in self.cursor.fetchall():
                folder_data['children'].append({
                    'type': 'service', 'id': service['id'], 'name': service['name'],
                    'children': self._fetch_accounts("service_id = ?", (service['id'],), sort_mode, descending),
                })
            result.append(folder_data)

        # Сервисы вне папок
        self.cursor.execute(f"SELECT * FROM services WHERE folder_id IS NULL ORDER BY {order}")
        for service in self.cursor.fetchall():
            result.append({
                'type': 'service', 'id': service['id'], 'name': service['name'],
                'children': self._fetch_accounts("service_id = ?", (service['id'],), sort_mode, descending),
            })

        # Свободные аккаунты (без сервиса)
        for node in self._fetch_accounts("service_id IS NULL", (), sort_mode, descending):
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
        Аккаунты в корзине исключаются."""
        self.cursor.execute("SELECT id FROM accounts WHERE deleted_at IS NULL")
        rows = [{"id": r["id"], "name": self.get_account_path(r["id"])}
                for r in self.cursor.fetchall()]
        rows.sort(key=lambda r: r["name"].lower())
        return rows

    def get_links(self, account_id):
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

    def set_links(self, account_id, target_ids):
        """Задаёт связи аккаунта (симметрично). Старые связи этого аккаунта заменяются."""
        with self.conn:
            self.cursor.execute(
                "DELETE FROM linked_accounts WHERE account_id = ? OR linked_account_id = ?",
                (account_id, account_id),
            )
            for t in target_ids:
                if t != account_id:
                    self.cursor.execute(
                        "INSERT INTO linked_accounts (account_id, linked_account_id) VALUES (?, ?)",
                        (account_id, t),
                    )
        self._mark_dirty()

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
            "SELECT description, image_data FROM gallery WHERE account_id = ? ORDER BY id",
            (account_id,),
        )
        gallery = [
            {
                "desc": r["description"] or "",
                "data": bytes(r["image_data"]) if r["image_data"] is not None else None,
            }
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

    def save_account(self, account_id, data):
        """Сохраняет полную карточку аккаунта. data — словарь примитивов в
        формате load_account(). Связанные таблицы перезаписываются целиком."""
        with self.conn:
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
        self._mark_dirty()

# Тестовая функция
def test_database():
    db = Database("test_hranilka.db")
    db.connect()
    db.create_tables()
    
    # Добавляем тестовые данные
    folder_id = db.add_folder("Личное")
    service_id = db.add_service("Google", folder_id)
    db.add_account(service_id, "personal@gmail.com", "user1", "pass123")
    db.add_account(service_id, "work@gmail.com", "user2", "pass456")
    
    # Получаем структуру
    structure = db.get_tree_structure()
    print("\n📊 Структура базы данных:")
    print(json.dumps(structure, indent=2, ensure_ascii=False))
    
    db.close()
    print("\n✅ Тест базы данных завершен!")

if __name__ == "__main__":
    test_database()