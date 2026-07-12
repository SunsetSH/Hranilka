"""Операции над деревом: папки/сервисы/аккаунты как узлы, сортировка,
перемещения, избранное, корзина, пути. Часть класса Database
(database.py) — методы вынесены дословно (backlog-разрез CRUD)."""
import logging
from datetime import datetime
from typing import Any, Optional
from hranilka.core.domain import days_until_password_change
from hranilka.core.fin_domain import days_until_expiry
from hranilka.core.fin_types import FIN_TYPES
from hranilka.core.nodetypes import ACCOUNT, FIN_LEAF_TYPES
from hranilka.data.database.state import DbBase


class DbTreeOpsMixin(DbBase):
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

    # ----- Финансовые листья дерева (карты/кошельки) -----

    @staticmethod
    def _build_fin_node(row) -> Optional[dict]:
        """Узел финансовой записи для дерева. item_type → node_type через
        дескриптор (FIN_TYPES); days_until_expiry — из экстракт-колонки.
        Неизвестный тип (не в реестре) → None — узел пропускается вызывающим
        (иначе node_type без записи в PREFIX уронил бы отрисовку дерева)."""
        spec = FIN_TYPES.get(row['item_type'])
        if spec is None:
            return None
        return {
            'type': spec.node_type,
            'id': row['id'],
            'name': row['name'],
            'is_favorite': bool(row['is_favorite']),
            'card_last4': row['card_last4'],
            'days_until_expiry': days_until_expiry(row['expires_on']),
        }

    def _sorted_fin_items(self, rows, sort_mode, descending=False):
        """Сортирует финансовые записи по режиму. Избранные всегда сверху.
        Режим pwd_due к финансам не применяется — остаётся порядок из SQL.
        Записи неизвестного типа отсеиваются (_build_fin_node вернул None)."""
        nodes = [n for n in (self._build_fin_node(r) for r in rows)
                 if n is not None]
        if sort_mode == "name":
            nodes.sort(key=lambda n: n['name'].lower(), reverse=descending)
        # "created" — порядок задан в SQL; "manual"/"pwd_due" — по sort_order из SQL
        nodes.sort(key=lambda n: not n['is_favorite'])
        return nodes

    # Колонки fin_items, нужные дереву (без тяжёлого data JSON).
    _TREE_FIN_COLUMNS = (
        "id", "item_type", "name", "is_favorite", "service_id",
        "card_last4", "expires_on",
    )

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

    def get_tree_structure(self, sort_mode="manual", descending=False,
                           include_fin=True):
        """Структура дерева. sort_mode: manual | name | created | pwd_due.
        Верхний уровень: папки, затем сервисы вне папок, затем свободные аккаунты.

        include_fin=False — финансовые листья (карты/кошельки) не выбираются из
        БД и не попадают в дерево (опция «Показывать фин. инструменты» выключена).

        Один запрос на тип (папки/сервисы/аккаунты) вместо запроса на каждый
        контейнер — устранение N+1 (на больших базах было десятки SELECT'ов)."""
        order = self._container_order(sort_mode, descending)
        if sort_mode == "created":
            acc_order = "created_at " + ("DESC" if descending else "ASC")
            fin_order = "created_at " + ("DESC" if descending else "ASC")
        else:
            acc_order = "sort_order, account_name"
            fin_order = "sort_order, name"

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
        # Финансовые записи — листья того же дерева, братья аккаунтов (концепт §9).
        # При include_fin=False фин-выборку пропускаем на стороне БД.
        fin_items: list = []
        if include_fin:
            fin_cols = ", ".join(self._TREE_FIN_COLUMNS)
            self.cursor.execute(
                f"SELECT {fin_cols} FROM fin_items WHERE deleted_at IS NULL "
                f"ORDER BY {fin_order}")
            fin_items = self.cursor.fetchall()
        # Неизвестные типы (не в реестре) пропускаются в дереве — один warning на
        # тип за вызов, чтобы не спамить лог по каждой записи.
        unknown_types = {it["item_type"] for it in fin_items
                         if it["item_type"] not in FIN_TYPES}
        if unknown_types:
            logging.warning(
                "Финансовые записи неизвестных типов пропущены в дереве: %s",
                ", ".join(sorted(unknown_types)))

        # Группируем в памяти, сохраняя порядок выборки (важно для режима
        # "created" и базового sort_order, поверх которых _sorted_accounts
        # доводит сортировку по имени/просрочке и поднимает избранные).
        accounts_by_service: dict[Any, list] = {}
        for a in accounts:
            accounts_by_service.setdefault(a["service_id"], []).append(a)
        fin_by_service: dict[Any, list] = {}
        for it in fin_items:
            fin_by_service.setdefault(it["service_id"], []).append(it)
        services_by_folder: dict[Any, list] = {}
        for s in services:
            services_by_folder.setdefault(s["folder_id"], []).append(s)

        def leaf_nodes(service_id):
            # Аккаунты, затем финансовые записи (братья внутри одного контейнера).
            return (self._sorted_accounts(
                        accounts_by_service.get(service_id, []), sort_mode, descending)
                    + self._sorted_fin_items(
                        fin_by_service.get(service_id, []), sort_mode, descending))

        def service_node(s):
            return {'type': 'service', 'id': s['id'], 'name': s['name'],
                    'children': leaf_nodes(s['id'])}

        result = []
        for folder in folders:
            result.append({
                'type': 'folder', 'id': folder['id'], 'name': folder['name'],
                'children': [service_node(s)
                             for s in services_by_folder.get(folder['id'], [])],
            })
        for s in services_by_folder.get(None, []):     # сервисы вне папок
            result.append(service_node(s))
        for node in leaf_nodes(None):                    # свободные записи (в корне)
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
        """Количество записей в корзине (аккаунты + финансовые записи)."""
        self.cursor.execute(
            "SELECT COUNT(*) AS n FROM accounts WHERE deleted_at IS NOT NULL")
        n = self.cursor.fetchone()["n"]
        self.cursor.execute(
            "SELECT COUNT(*) AS n FROM fin_items WHERE deleted_at IS NOT NULL")
        return n + self.cursor.fetchone()["n"]

    def get_deleted_accounts(self):
        """Список аккаунтов в корзине (последние удалённые — сверху).

        Сигнатура сохранена для обратной совместимости с ui/dialogs/recycle_bin.py
        (возвращает только аккаунты). Финансовые записи — get_deleted_fin_items;
        общая выдача обоих типов — get_deleted_records."""
        self.cursor.execute(
            "SELECT id, account_name, deleted_at FROM accounts "
            "WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC, id DESC"
        )
        return [{"id": r["id"], "name": r["account_name"],
                 "deleted_at": r["deleted_at"]} for r in self.cursor.fetchall()]

    def get_deleted_fin_items(self):
        """Список финансовых записей в корзине (последние удалённые — сверху).
        Каждая: id, type (node_type), item_type, name, deleted_at."""
        self.cursor.execute(
            "SELECT id, item_type, name, deleted_at FROM fin_items "
            "WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC, id DESC"
        )
        result = []
        for r in self.cursor.fetchall():
            spec = FIN_TYPES.get(r["item_type"])
            result.append({
                "id": r["id"],
                "type": spec.node_type if spec else r["item_type"],
                "item_type": r["item_type"],
                "name": r["name"],
                "deleted_at": r["deleted_at"],
            })
        return result

    def get_deleted_records(self):
        """Обобщённая выдача корзины: аккаунты и финансовые записи вместе,
        каждая с полем type ('account'/'card'/'wallet'). Сортировка по времени
        удаления (последние — сверху)."""
        records = [{"type": "account", "id": r["id"], "name": r["name"],
                    "deleted_at": r["deleted_at"]}
                   for r in self.get_deleted_accounts()]
        records += [{"type": r["type"], "id": r["id"], "name": r["name"],
                     "deleted_at": r["deleted_at"]}
                    for r in self.get_deleted_fin_items()]
        # Последние удалённые — сверху (deleted_at убыв.; None — в конец).
        records.sort(key=lambda x: (x["deleted_at"] or ""), reverse=True)
        return records

    def empty_bin(self):
        """Безвозвратно удаляет все записи из корзины (аккаунты и финансовые)."""
        with self.conn:
            self.cursor.execute(
                "DELETE FROM accounts WHERE deleted_at IS NOT NULL")
            self.cursor.execute(
                "DELETE FROM fin_items WHERE deleted_at IS NOT NULL")
        self._invalidate_gallery_bytes()   # каскад мог удалить картинки (M-9)
        self._mark_dirty()

    # ----- Удаление с сохранением содержимого -----

    def _delete_folder_keep_content_rows(self, folder_id):
        """Тело delete_folder_keep_content без транзакции/dirty."""
        self.cursor.execute(
            "UPDATE services SET folder_id = NULL WHERE folder_id = ?", (folder_id,)
        )
        self.cursor.execute("DELETE FROM folders WHERE id = ?", (folder_id,))

    def _delete_service_keep_content_rows(self, service_id):
        """Тело delete_service_keep_content без транзакции/dirty.

        И аккаунты, и финансовые записи — содержимое сервиса. Их нужно вынести
        до удаления строки services, иначе FK ``ON DELETE CASCADE`` уничтожит
        fin_items вместе с картинками и связями.
        """
        self.cursor.execute(
            "UPDATE accounts SET service_id = NULL WHERE service_id = ?", (service_id,)
        )
        self.cursor.execute(
            "UPDATE fin_items SET service_id = NULL WHERE service_id = ?", (service_id,)
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

    def _descendant_leaf_keys_rows(self, node_type, node_id):
        """Ключи всех листьев внутри контейнера до его каскадного удаления.

        Возвращает типизированные ``(node_type, id)``: id аккаунта и fin_items
        находятся в разных таблицах и могут совпадать, поэтому голого id для
        очистки UI-черновиков недостаточно.
        """
        if node_type == "service":
            self.cursor.execute("SELECT id FROM accounts WHERE service_id = ?", (node_id,))
            account_ids = [r["id"] for r in self.cursor.fetchall()]
            self.cursor.execute(
                "SELECT id, item_type FROM fin_items WHERE service_id = ?", (node_id,))
        elif node_type == "folder":
            self.cursor.execute(
                "SELECT id FROM accounts WHERE service_id IN "
                "(SELECT id FROM services WHERE folder_id = ?)", (node_id,))
            account_ids = [r["id"] for r in self.cursor.fetchall()]
            self.cursor.execute(
                "SELECT id, item_type FROM fin_items WHERE service_id IN "
                "(SELECT id FROM services WHERE folder_id = ?)", (node_id,))
        else:
            return []
        keys = [("account", aid) for aid in account_ids]
        for row in self.cursor.fetchall():
            spec = FIN_TYPES.get(row["item_type"])
            if spec is not None:
                keys.append((spec.node_type, row["id"]))
        return keys

    def delete_items(self, items, keep=False, to_bin=False):
        """Атомарно удаляет набор узлов дерева в ОДНОЙ транзакции (M7-04).

        items — список кортежей (type, id) в заданном порядке, где type это
        "folder" | "service" | "account" | "card" | "wallet" (финансовые листья).
        keep=True — удалять контейнеры с сохранением содержимого (на уровень
        выше). to_bin=True — листья отправлять в корзину (мягко), а не удалять.

        Возвращает типизированные ключи реально удалённых/перемещённых в
        корзину листьев (для очистки UI-кэша несохранённых правок ПОСЛЕ
        успеха)."""
        affected: list[tuple[str, int]] = []
        gallery_touched = False
        with self.conn:
            for node_type, node_id in items:
                if node_type == "folder":
                    if keep:
                        self._delete_folder_keep_content_rows(node_id)
                    else:
                        affected += self._descendant_leaf_keys_rows("folder", node_id)
                        self._delete_folder_rows(node_id)
                        gallery_touched = True
                elif node_type == "service":
                    if keep:
                        self._delete_service_keep_content_rows(node_id)
                    else:
                        affected += self._descendant_leaf_keys_rows("service", node_id)
                        self._delete_service_rows(node_id)
                        gallery_touched = True
                elif node_type in FIN_LEAF_TYPES:       # card | wallet
                    if to_bin:
                        self._move_fin_item_to_bin_rows(node_id)
                    else:
                        self._delete_fin_item_rows(node_id)
                        gallery_touched = True
                    affected.append((node_type, node_id))
                elif node_type == ACCOUNT:
                    if to_bin:
                        self._move_account_to_bin_rows(node_id)
                    else:
                        self._delete_account_rows(node_id)
                        gallery_touched = True
                    affected.append(("account", node_id))
                else:
                    # Тип вне дерева (folder/service/account/FIN_LEAF_TYPES) —
                    # ошибка вызывающей стороны. Раньше это молча роутилось в
                    # ветку account и удаляло чужой аккаунт с совпадающим id
                    # (записи в корзине неизвестного/убранного из реестра
                    # фин-типа отдавали свой сырой item_type как node_type).
                    raise ValueError(f"Неизвестный тип узла: {node_type!r}")
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

