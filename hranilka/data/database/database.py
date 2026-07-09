"""Database — ядро слоя данных: CRUD и сборка класса из mixin-модулей.

Класс разрезан по ответственностям (этап 2 реструктуризации):
  errors.py      — доменные исключения;
  concurrency.py — RLock + executor + поколения сессий (async-доступ);
  persistence.py — файл/контейнер AES-GCM, отложенная запись, шифрование;
  schema.py      — DDL, версия схемы, миграции, pre-migrate-копии;
  здесь          — CRUD (папки/сервисы/аккаунты/связи/галерея, корзина,
                   bulk-выгрузки) и авто-обёртка публичных методов в лок
                   (см. конец файла).

Имена SCHEMA_VERSION и исключений реэкспортируются отсюда — внешний код
по-прежнему импортирует их из этого модуля."""
import functools
import inspect
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from hranilka.core.domain import days_until_password_change, canonical_link_pair

from hranilka.data.database.concurrency import DbConcurrencyMixin
from hranilka.data.errors import (FutureSchemaError, PreMigrationBackupError,
                                  StaleSessionError, VaultConflictError)
from hranilka.data.database.persistence import DbPersistenceMixin
from hranilka.data.database.schema import (SCHEMA_VERSION, _REQUIRED_TABLES,
                                  DbSchemaMixin)

__all__ = ["Database", "SCHEMA_VERSION", "FutureSchemaError",
           "PreMigrationBackupError", "StaleSessionError",
           "VaultConflictError"]


class Database(DbConcurrencyMixin, DbPersistenceMixin, DbSchemaMixin):
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

    def _invalidate_gallery_bytes(self):
        """Сбросить кэш суммарного объёма галереи (после любой её мутации)."""
        self._gallery_bytes = None

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
        self._invalidate_gallery_bytes()   # галерея очищена (M-9)
        self._mark_dirty()
            
    def _add_folder_rows(self, name: str) -> int:
        """Тело add_folder без транзакции/dirty (для составных методов)."""
        self.cursor.execute("INSERT INTO folders (name) VALUES (?)", (name,))
        return int(self.cursor.lastrowid)

    def _add_service_rows(self, name: str, folder_id: int | None = None) -> int:
        """Тело add_service без транзакции/dirty (для составных методов)."""
        self.cursor.execute(
            "INSERT INTO services (name, folder_id) VALUES (?, ?)",
            (name, folder_id))
        return int(self.cursor.lastrowid)

    def _add_account_rows(self, service_id: int | None, account_name: str,
                          login: str | None = None,
                          password: str | None = None) -> int:
        """Тело add_account без транзакции/dirty (для составных методов).
        created_at пишем строкой в формате SQLite CURRENT_TIMESTAMP
        (yyyy-MM-dd HH:mm:ss): datetime как SQL-параметр даёт DeprecationWarning
        на 3.12+ и тот же формат ожидают парсеры (models._DT_FORMAT) (L-1)."""
        self.cursor.execute(
            "INSERT INTO accounts (service_id, account_name, login, password, created_at) VALUES (?, ?, ?, ?, ?)",
            (service_id, account_name, login, password,
             datetime.now().isoformat(" ", "seconds")))
        return int(self.cursor.lastrowid)

    def add_folder(self, name: str) -> int:
        """Добавление папки"""
        with self.conn:
            new_id = self._add_folder_rows(name)
        self._mark_dirty()
        return new_id

    def add_service(self, name: str, folder_id: int | None = None) -> int:
        """Добавление сервиса"""
        with self.conn:
            new_id = self._add_service_rows(name, folder_id)
        self._mark_dirty()
        return new_id

    def add_account(self, service_id: int | None, account_name: str,
                    login: str | None = None, password: str | None = None) -> int:
        """Добавление аккаунта"""
        with self.conn:
            new_id = self._add_account_rows(service_id, account_name,
                                            login, password)
        self._mark_dirty()
        return new_id
    
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

    # Колонки accounts, нужные для построения/сортировки дерева (M-5). Тяжёлые
    # текстовые поля (password, notes, extra_info, url, ip и т.д.) в дерево не
    # входят — выбираем только используемые в _build_account_node/_sorted_accounts
    # и группировке (service_id). created_at нужен для сортировки "created".
    _TREE_ACCOUNT_COLUMNS = (
        "id", "account_name", "login", "is_favorite", "service_id",
        "password_changed_date", "password_change_interval_days",
    )

    def _fetch_accounts(self, where_clause, params, sort_mode, descending):
        if sort_mode == "created":
            order = "created_at " + ("DESC" if descending else "ASC")
        else:
            order = "sort_order, account_name"
        cols = ", ".join(self._TREE_ACCOUNT_COLUMNS)
        # Аккаунты в корзине (deleted_at не пуст) в дереве не показываем.
        self.cursor.execute(
            f"SELECT {cols} FROM accounts WHERE ({where_clause}) AND deleted_at IS NULL "
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

        self.cursor.execute(f"SELECT id, name FROM folders ORDER BY {order}")
        folders = self.cursor.fetchall()
        self.cursor.execute(f"SELECT id, name, folder_id FROM services ORDER BY {order}")
        services = self.cursor.fetchall()
        # Аккаунты в корзине (deleted_at не пуст) в дереве не показываем.
        # Только колонки, нужные дереву (M-5) — без password/notes/extra_info.
        acc_cols = ", ".join(self._TREE_ACCOUNT_COLUMNS)
        self.cursor.execute(
            f"SELECT {acc_cols} FROM accounts WHERE deleted_at IS NULL ORDER BY {acc_order}"
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
        self._write("UPDATE folders SET name = ? WHERE id = ?", (name, folder_id))

    def rename_service(self, service_id, name):
        self._write("UPDATE services SET name = ? WHERE id = ?", (name, service_id))

    # ----- Удаление без сохранения содержимого (полностью) -----

    def _delete_folder_rows(self, folder_id):
        """Тело delete_folder без транзакции/dirty (для составных методов)."""
        # services.folder_id имеет ON DELETE SET NULL, поэтому удаляем
        # сервисы явно (их аккаунты уйдут каскадом), затем папку.
        self.cursor.execute("SELECT id FROM services WHERE folder_id = ?", (folder_id,))
        for row in self.cursor.fetchall():
            self.cursor.execute("DELETE FROM services WHERE id = ?", (row["id"],))
        self.cursor.execute("DELETE FROM folders WHERE id = ?", (folder_id,))

    def _delete_service_rows(self, service_id):
        """Тело delete_service без транзакции/dirty (для составных методов)."""
        self.cursor.execute("DELETE FROM services WHERE id = ?", (service_id,))

    def _delete_account_rows(self, account_id):
        """Тело delete_account без транзакции/dirty (для составных методов)."""
        self.cursor.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def delete_folder(self, folder_id):
        """Удаляет папку вместе со всеми сервисами и их аккаунтами."""
        with self.conn:
            self._delete_folder_rows(folder_id)
        self._invalidate_gallery_bytes()   # каскад мог удалить картинки (M-9)
        self._mark_dirty()

    def delete_service(self, service_id):
        """Удаляет сервис вместе с его аккаунтами (каскад по FK)."""
        self._write("DELETE FROM services WHERE id = ?", (service_id,))
        self._invalidate_gallery_bytes()   # каскад мог удалить картинки (M-9)

    def delete_account(self, account_id):
        """Удаляет аккаунт и все связанные данные (каскад по FK)."""
        self._write("DELETE FROM accounts WHERE id = ?", (account_id,))
        self._invalidate_gallery_bytes()   # каскад мог удалить картинки (M-9)

    # ----- Корзина (мягкое удаление аккаунтов) -----

    def _is_in_bin(self, account_id):
        """True, если аккаунт лежит в корзине (помечен как удалённый)."""
        self.cursor.execute(
            "SELECT deleted_at FROM accounts WHERE id = ?", (account_id,)
        )
        row = self.cursor.fetchone()
        return bool(row and row["deleted_at"])

    def _move_account_to_bin_rows(self, account_id):
        """Тело move_account_to_bin без транзакции/dirty (для составных методов)."""
        self.cursor.execute(
            "UPDATE accounts SET deleted_at = ? WHERE id = ?",
            (datetime.now().isoformat(" ", "seconds"), account_id),
        )

    def move_account_to_bin(self, account_id):
        """Переносит аккаунт в корзину (мягкое удаление): данные сохраняются,
        но аккаунт скрыт из дерева и связей до восстановления."""
        self._write(
            "UPDATE accounts SET deleted_at = ? WHERE id = ?",
            (datetime.now().isoformat(" ", "seconds"), account_id),
        )

    def restore_account(self, account_id):
        """Восстанавливает аккаунт из корзины."""
        self._write(
            "UPDATE accounts SET deleted_at = NULL WHERE id = ?", (account_id,))

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
        self._write("DELETE FROM accounts WHERE deleted_at IS NOT NULL")
        self._invalidate_gallery_bytes()   # каскад мог удалить картинки (M-9)

    # ----- Удаление с сохранением содержимого -----

    def _delete_folder_keep_content_rows(self, folder_id):
        """Тело delete_folder_keep_content без транзакции/dirty."""
        self.cursor.execute(
            "UPDATE services SET folder_id = NULL WHERE folder_id = ?", (folder_id,)
        )
        self.cursor.execute("DELETE FROM folders WHERE id = ?", (folder_id,))

    def _delete_service_keep_content_rows(self, service_id):
        """Тело delete_service_keep_content без транзакции/dirty."""
        self.cursor.execute(
            "UPDATE accounts SET service_id = NULL WHERE service_id = ?", (service_id,)
        )
        self.cursor.execute("DELETE FROM services WHERE id = ?", (service_id,))

    def delete_folder_keep_content(self, folder_id):
        """Удаляет папку, но её сервисы становятся самостоятельными (вне папки)."""
        with self.conn:
            self._delete_folder_keep_content_rows(folder_id)
        self._mark_dirty()

    def delete_service_keep_content(self, service_id):
        """Удаляет сервис, но его аккаунты становятся свободными (без сервиса)."""
        with self.conn:
            self._delete_service_keep_content_rows(service_id)
        self._mark_dirty()

    def _descendant_account_ids_rows(self, node_type, node_id):
        """Тело get_descendant_account_ids без лока (для составных методов)."""
        if node_type == "service":
            self.cursor.execute("SELECT id FROM accounts WHERE service_id = ?", (node_id,))
        elif node_type == "folder":
            self.cursor.execute(
                "SELECT id FROM accounts WHERE service_id IN "
                "(SELECT id FROM services WHERE folder_id = ?)", (node_id,))
        else:
            return []
        return [r["id"] for r in self.cursor.fetchall()]

    def delete_items(self, items, keep=False, to_bin=False):
        """Атомарно удаляет набор узлов дерева в ОДНОЙ транзакции (M7-04).

        items — список кортежей (type, id) в заданном порядке, где type это
        "folder" | "service" | "account". keep=True — удалять контейнеры с
        сохранением содержимого (на уровень выше). to_bin=True — аккаунты
        отправлять в корзину (мягко), а не удалять.

        Возвращает список id аккаунтов, реально удалённых/перемещённых в
        корзину (для очистки UI-кэша несохранённых правок ПОСЛЕ успеха)."""
        affected: list = []
        gallery_touched = False
        with self.conn:
            for node_type, node_id in items:
                if node_type == "folder":
                    if keep:
                        self._delete_folder_keep_content_rows(node_id)
                    else:
                        affected += self._descendant_account_ids_rows("folder", node_id)
                        self._delete_folder_rows(node_id)
                        gallery_touched = True
                elif node_type == "service":
                    if keep:
                        self._delete_service_keep_content_rows(node_id)
                    else:
                        affected += self._descendant_account_ids_rows("service", node_id)
                        self._delete_service_rows(node_id)
                        gallery_touched = True
                else:                                   # account
                    if to_bin:
                        self._move_account_to_bin_rows(node_id)
                    else:
                        self._delete_account_rows(node_id)
                        gallery_touched = True
                    affected.append(node_id)
        if gallery_touched:
            self._invalidate_gallery_bytes()   # каскад мог удалить картинки (M-9)
        self._mark_dirty()
        return affected

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

    def _move_service_rows(self, service_id, folder_id):
        """Тело move_service без транзакции/dirty (для составных методов)."""
        order = self._next_sort_order("services", "folder_id", folder_id)
        self.cursor.execute(
            "UPDATE services SET folder_id = ?, sort_order = ? WHERE id = ?",
            (folder_id, order, service_id),
        )

    def _move_account_rows(self, account_id, service_id):
        """Тело move_account без транзакции/dirty (для составных методов)."""
        order = self._next_sort_order("accounts", "service_id", service_id)
        self.cursor.execute(
            "UPDATE accounts SET service_id = ?, sort_order = ? WHERE id = ?",
            (service_id, order, account_id),
        )

    def _set_favorite_rows(self, account_id, value):
        """Тело set_favorite без транзакции/dirty (для составных методов)."""
        self.cursor.execute(
            "UPDATE accounts SET is_favorite = ? WHERE id = ?",
            (1 if value else 0, account_id))

    def move_service(self, service_id, folder_id):
        """Переносит сервис в папку (folder_id=None — вынести из папки), в конец списка."""
        with self.conn:
            self._move_service_rows(service_id, folder_id)
        self._mark_dirty()

    def move_account(self, account_id, service_id):
        """Переносит аккаунт в сервис (service_id=None — сделать свободным), в конец списка."""
        with self.conn:
            self._move_account_rows(account_id, service_id)
        self._mark_dirty()

    def set_favorite(self, account_id, value):
        with self.conn:
            self._set_favorite_rows(account_id, value)
        self._mark_dirty()

    # ----- Составные (атомарные) операции с деревом (M7-04) -----

    def move_services(self, service_ids, folder_id):
        """Пакетно переносит сервисы в папку в ОДНОЙ транзакции (M7-04)."""
        with self.conn:
            for sid in service_ids:
                self._move_service_rows(sid, folder_id)
        self._mark_dirty()

    def move_accounts(self, account_ids, service_id):
        """Пакетно переносит аккаунты в сервис в ОДНОЙ транзакции (M7-04)."""
        with self.conn:
            for aid in account_ids:
                self._move_account_rows(aid, service_id)
        self._mark_dirty()

    def set_favorites(self, account_ids, value):
        """Пакетно проставляет/снимает избранное в ОДНОЙ транзакции (M7-04)."""
        with self.conn:
            for aid in account_ids:
                self._set_favorite_rows(aid, value)
        self._mark_dirty()

    def move_services_to_new_folder(self, name, service_ids):
        """Создаёт папку и переносит в неё сервисы в ОДНОЙ транзакции (M7-04).
        При сбое любого шага папка не создаётся, ни один сервис не перемещён.
        Возвращает id созданной папки."""
        with self.conn:
            folder_id = self._add_folder_rows(name)
            for sid in service_ids:
                self._move_service_rows(sid, folder_id)
        self._mark_dirty()
        return folder_id

    def move_accounts_to_new_service(self, name, account_ids):
        """Создаёт сервис и переносит в него аккаунты в ОДНОЙ транзакции (M7-04).
        При сбое любого шага сервис не создаётся, ни один аккаунт не перемещён.
        Возвращает id созданного сервиса."""
        with self.conn:
            service_id = self._add_service_rows(name, None)
            for aid in account_ids:
                self._move_account_rows(aid, service_id)
        self._mark_dirty()
        return service_id

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
        # COLLATE NOCASE — единый регистронезависимый порядок с _container_order (L-4).
        self.cursor.execute("SELECT id, name FROM folders ORDER BY name COLLATE NOCASE")
        return [{"id": r["id"], "name": r["name"]} for r in self.cursor.fetchall()]

    def get_services(self):
        self.cursor.execute("SELECT id, name FROM services ORDER BY name COLLATE NOCASE")
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

    def _gallery_total_all(self) -> int:
        """Полный SUM(LENGTH(image_data)) по галерее с мемоизацией (M-9).

        Значение считается один раз и хранится в self._gallery_bytes до первой
        мутации галереи или смены сессии (там кэш сбрасывается в None)."""
        if self._gallery_bytes is None:
            self.cursor.execute(
                "SELECT COALESCE(SUM(LENGTH(image_data)), 0) AS s FROM gallery")
            self._gallery_bytes = int(self.cursor.fetchone()["s"])
        return self._gallery_bytes

    def gallery_total_bytes(self, exclude_account_id: int | None = None) -> int:
        """Суммарный объём всех картинок в галерее (в байтах).

        exclude_account_id — исключить указанный аккаунт из суммы: его картинки
        обычно держатся в памяти редактируемой карточки, и учитывать их повторно
        при проверке лимита суммарного объёма не нужно (M3-05).

        Полный объём кэшируется (M-9); при exclude вычитаем объём одного аккаунта
        (дешёвый запрос по индексу idx_gallery_account) из кэшированной суммы."""
        total = self._gallery_total_all()
        if exclude_account_id is None:
            return total
        self.cursor.execute(
            "SELECT COALESCE(SUM(LENGTH(image_data)), 0) AS s "
            "FROM gallery WHERE account_id = ?", (exclude_account_id,))
        return total - int(self.cursor.fetchone()["s"])

    def get_links(self, account_id: int) -> list[dict[str, Any]]:
        """Связанные аккаунты (в обе стороны) с путями.

        Пути строятся из заранее загруженных карт имён (_name_maps — 3 запроса),
        а не вызовом get_account_path()/_is_in_bin() на каждый связанный id (было
        N+1: до 4 запросов на связь). Формат результата идентичен прежнему (M-4)."""
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
        if not ids:
            return []
        acc, svc, fld = self._name_maps()
        result = []
        for i in ids:
            a = acc.get(i)
            # Нет записи или аккаунт в корзине (deleted_at не пуст) — не показываем.
            if not a or a[2]:
                continue
            result.append({"id": i, "name": self._path_from_maps(i, acc, svc, fld)})
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
            for k in ("mobile_phone", "first_name", "last_name", "middle_name",
                      "birth_date", "address")
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
            "SELECT id, description, LENGTH(image_data) AS blob_size "
            "FROM gallery WHERE account_id = ? ORDER BY id",
            (account_id,),
        )
        # image_id — id строки для контракта сохранения (H-6): вернув item с
        # data=None и этим image_id, UI сообщает «сохранить существующий BLOB».
        # Ключ "id" оставлен для обратной совместимости с прежними вызывателями.
        # blob_size — размер BLOB без чтения самих байтов (LENGTH по заголовку,
        # дёшево): нужен для учёта ленивых картинок в лимите общего объёма (M7-03),
        # иначе ещё не загруженные BLOB не считались бы и кап в 500 МБ можно было
        # незаметно превысить.
        gallery = [
            {"id": r["id"], "image_id": r["id"],
             "desc": r["description"] or "", "data": None,
             "blob_size": r["blob_size"]}
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
        формате load_account(). Связанные таблицы перезаписываются целиком.
        Возвращает список id строк галереи (см. _save_gallery_rows) — UI
        присваивает их новым картинкам, чтобы повторное сохранение не
        перезаливало их BLOB заново."""
        with self.conn:
            gallery_ids = self._save_account_rows(account_id, data)
        self._mark_dirty()
        return gallery_ids

    def add_account_with_card(self, service_id, name, storage) -> int:
        """Создаёт аккаунт и сразу пишет его карточку в ОДНОЙ транзакции (M7-04).
        Раньше add_account и save_account были двумя транзакциями: сбой второй
        оставлял полупустой аккаунт. Теперь либо обе, либо ни одна.
        Возвращает id созданного аккаунта (галерея у нового аккаунта пуста)."""
        with self.conn:
            account_id = self._add_account_rows(service_id, name)
            # rowcount-гард в _save_account_rows проверяет UPDATE по только что
            # вставленной строке — она есть, поэтому проходит (L-6).
            self._save_account_rows(account_id, storage)
        self._mark_dirty()
        return account_id

    def save_account_with_links(self, account_id, data, target_ids):
        """Атомарно сохраняет карточку и её связи В ОДНОЙ транзакции (H6-02):
        раньше save_account и set_links были двумя транзакциями — сбой второй
        оставлял карточку записанной, а связи нет. Теперь либо обе, либо ни одна.
        Возвращает список id строк галереи (как save_account)."""
        with self.conn:
            gallery_ids = self._save_account_rows(account_id, data)
            self._set_links_rows(account_id, target_ids)
        self._mark_dirty()
        return gallery_ids

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
        # Аккаунт мог быть удалён между открытием карточки и сохранением. Если
        # UPDATE не затронул ни одной строки — прерываем внутри транзакции, чтобы
        # `with self.conn` откатил уже вставленные связанные строки (L-6).
        if self.cursor.rowcount != 1:
            raise ValueError("Аккаунт не найден")

        p = data["personal"]
        self.cursor.execute("DELETE FROM personal_data WHERE account_id = ?", (account_id,))
        self.cursor.execute(
            """INSERT INTO personal_data
               (account_id, mobile_phone, first_name, last_name, middle_name,
                birth_date, address)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (account_id, p.get("mobile_phone"), p["first_name"], p["last_name"],
             p["middle_name"], p["birth_date"], p["address"]),
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

        return self._save_gallery_rows(account_id, data["gallery"])

    def _save_gallery_rows(self, account_id, items):
        """Согласует строки галереи аккаунта со списком items (контракт H-6).

        Каждый item — dict с ключами:
          * "desc" — подпись (метаданные);
          * "data" — bytes (новый/обновлённый BLOB) или None;
          * "image_id" — id существующей строки gallery (опционально).

        Правила:
          * data=None и задан image_id → «сохранить существующий BLOB»: строка
            НЕ удаляется и НЕ обнуляется; обновляется только description
            (image_data не трогаем);
          * data=bytes → вставить новую строку; если задан image_id — обновить
            BLOB и description существующей строки;
          * строки, чей id ОТСУТСТВУЕТ среди переданных image_id → удалить (это
            явное удаление картинки пользователем).

        Порядок сохраняется как прежде (load_account ORDER BY id): сохранённые
        строки удерживают свои id, новые вставляются в конец в порядке списка.

        Возвращает список id той же длины и порядка, что items: id строки в БД
        после сохранения либо None для пропущенного элемента (нет ни BLOB, ни
        существующей строки). UI по этому списку присваивает id новым картинкам,
        чтобы следующее сохранение обновляло строку, а не пересоздавало её."""
        # Кэш суммарного объёма галереи устаревает при любой мутации (M-9).
        self._invalidate_gallery_bytes()

        # id всех текущих строк галереи аккаунта — чтобы удалить отсутствующие
        # в новом списке и валидировать переданные image_id.
        self.cursor.execute(
            "SELECT id FROM gallery WHERE account_id = ?", (account_id,))
        existing_ids = {r["id"] for r in self.cursor.fetchall()}

        kept_ids = {g["image_id"] for g in items
                    if g.get("image_id") is not None and g["image_id"] in existing_ids}
        # Явно удалённые пользователем: были в БД, но их id нет в переданном списке.
        to_delete = existing_ids - kept_ids
        for row_id in to_delete:
            self.cursor.execute("DELETE FROM gallery WHERE id = ?", (row_id,))

        saved_ids: list[int | None] = []
        for g in items:
            image_id = g.get("image_id")
            desc = g.get("desc", "")
            data = g.get("data")
            if data is None:
                # «Сохранить существующий BLOB»: обновляем только подпись, не
                # трогая image_data. Если строки уже нет (гонка/чужой id) —
                # пропускаем (нечего сохранять, вставлять пустой BLOB не нужно).
                if image_id is not None and image_id in existing_ids:
                    self.cursor.execute(
                        "UPDATE gallery SET description = ? WHERE id = ?",
                        (desc, image_id))
                    saved_ids.append(image_id)
                else:
                    saved_ids.append(None)
                continue
            blob = sqlite3.Binary(data)
            if image_id is not None and image_id in existing_ids:
                # Обновление BLOB существующей строки (перезалитая картинка).
                self.cursor.execute(
                    "UPDATE gallery SET description = ?, image_data = ? WHERE id = ?",
                    (desc, blob, image_id))
                saved_ids.append(image_id)
            else:
                # Новая картинка — вставляем в конец (новый, больший id).
                self.cursor.execute(
                    "INSERT INTO gallery (account_id, description, image_data) VALUES (?, ?, ?)",
                    (account_id, desc, blob))
                saved_ids.append(int(self.cursor.lastrowid))
        return saved_ids

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
        personal_keys = ("mobile_phone", "first_name", "last_name", "middle_name",
                         "birth_date", "address")

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
