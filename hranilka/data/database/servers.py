"""VPS-серверы: CRUD карточки, связи сервер↔аккаунт, галерея, корзина,
избранное, сортировка. Часть класса Database (database.py).

Сущность полностью независима от финансовых записей (fin_items) — своя
таблица (servers), свои связи (server_links), своя галерея (server_gallery).
См. docs/ТЗ_VPS_Серверы.md §2.

Паттерн — как в fin_items.py/accounts.py: публичный метод = транзакция
(`with self.conn`) + self._mark_dirty(); приватный `_..._rows` = чистый SQL
для переиспользования под общей транзакцией. Экстракт-колонка paid_until
пересчитывается из payload при каждом сохранении через
server_data.extract_paid_until (единственный писатель — рассинхронизация с
JSON исключена по построению).
"""
import json
from datetime import datetime
from typing import Any, Optional

from hranilka.data.database.state import DbBase
from hranilka.data.errors import CorruptedPayloadError
from hranilka.data.models.server_data import extract_paid_until


class DbServersMixin(DbBase):
    # ----- Создание -----

    def _add_server_rows(self, service_id: Optional[int], name: str) -> int:
        """Тело add_server без транзакции/dirty (для составных методов).
        created_at — строкой формата SQLite CURRENT_TIMESTAMP (как в accounts/
        fin_items)."""
        self.cursor.execute(
            "INSERT INTO servers (service_id, name, data, created_at) "
            "VALUES (?, ?, '{}', ?)",
            (service_id, name, datetime.now().isoformat(" ", "seconds")))
        return int(self.cursor.lastrowid)

    def add_server(self, service_id: Optional[int], name: str) -> int:
        """Создаёт пустой сервер, возвращает его id."""
        with self.conn:
            new_id = self._add_server_rows(service_id, name)
        self._mark_dirty()
        return new_id

    def count_servers(self) -> int:
        """Всего серверов в БД, включая корзину (для предупреждения при
        выключении показа серверов). Без фильтра deleted_at."""
        self.cursor.execute("SELECT COUNT(*) AS n FROM servers")
        return int(self.cursor.fetchone()["n"])

    def count_active_servers(self) -> int:
        """Серверы вне корзины (deleted_at IS NULL)."""
        self.cursor.execute(
            "SELECT COUNT(*) AS n FROM servers WHERE deleted_at IS NULL")
        return int(self.cursor.fetchone()["n"])

    # ----- Загрузка/сохранение карточки -----

    def get_server(self, server_id: int) -> Optional[dict[str, Any]]:
        """Полная карточка сервера в формате storage (см.
        models.ServerData.from_storage) плюс service_id и связанные аккаунты,
        или None, если сервер не найден. BLOB галереи не читается — ленивая
        загрузка (контракт H-6): вернётся data=None + image_id + blob_size."""
        self.cursor.execute("SELECT * FROM servers WHERE id = ?", (server_id,))
        row = self.cursor.fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["data"]) if row["data"] else {}
        except (ValueError, TypeError) as e:
            # Повреждённый JSON — НЕ подменяем молча пустым словарём (M-03):
            # иначе пользователь мог бы сохранить пустую карточку поверх
            # потенциально восстановимых секретов. Строка данных намеренно не
            # попадает в исключение/лог (CorruptedPayloadError.__doc__).
            # Вызыватель (UI) обязан открыть карточку fail-closed и оставить
            # строку в БД нетронутой.
            raise CorruptedPayloadError(server_id) from e

        self.cursor.execute(
            "SELECT id, description, LENGTH(image_data) AS blob_size "
            "FROM server_gallery WHERE server_id = ? ORDER BY id", (server_id,))
        gallery = [
            {"id": r["id"], "image_id": r["id"], "desc": r["description"] or "",
             "data": None, "blob_size": r["blob_size"]}
            for r in self.cursor.fetchall()
        ]
        return {"name": row["name"], "service_id": row["service_id"],
                "payload": payload, "gallery": gallery,
                "account_ids": self.get_server_links(server_id)}

    def _save_server_rows(self, server_id: int,
                          storage: dict[str, Any]) -> list:
        """Тело save_server без транзакции/dirty. Пишет name/data и
        пересчитанную экстракт-колонку paid_until, затем согласует галерею."""
        name = storage.get("name") or ""
        payload = storage.get("payload") or {}
        paid_until = extract_paid_until(payload)
        data_json = json.dumps(payload, ensure_ascii=False)
        self.cursor.execute(
            "UPDATE servers SET name = ?, data = ?, paid_until = ? WHERE id = ?",
            (name, data_json, paid_until, server_id))
        # Сервер мог быть удалён между открытием и сохранением: если UPDATE не
        # затронул строку — прерываем внутри транзакции (откат галереи, L-6).
        if self.cursor.rowcount != 1:
            raise ValueError("Сервер не найден")
        return self._save_server_gallery_rows(server_id, storage.get("gallery") or [])

    def save_server(self, server_id: int, storage: dict[str, Any]) -> list:
        """Сохраняет карточку сервера. Возвращает id строк галереи (как
        save_account/save_fin_item — для присвоения id новым картинкам)."""
        with self.conn:
            gallery_ids = self._save_server_rows(server_id, storage)
        self._mark_dirty()
        return gallery_ids

    def save_server_with_links(self, server_id: int, storage: dict[str, Any],
                               account_ids: Optional[list[int]] = None) -> list:
        """Атомарно сохраняет карточку и её связи в ОДНОЙ транзакции (по
        образцу save_fin_item_with_links): либо обе, либо ни одна.
        account_ids=None — server_links НЕ трогаются: черновик с неизвестными
        связями (H-02: ошибка чтения get_server_links при осиротевшей
        загрузке галереи) не должен при сохранении молча стереть реальные
        привязки сервер↔аккаунт. Пустой список [] — легитимное «снять все
        связи», отличается от None по построению."""
        with self.conn:
            gallery_ids = self._save_server_rows(server_id, storage)
            if account_ids is not None:
                self._set_server_links_rows(server_id, account_ids)
        self._mark_dirty()
        return gallery_ids

    # ----- Галерея (тонкие обёртки над общей логикой gallery_ops) -----

    def load_server_gallery_image(self, image_id: int):
        """BLOB одной строки server_gallery (ленивая загрузка миниатюры)."""
        return self._load_gallery_image("server_gallery", image_id)

    def _save_server_gallery_rows(self, server_id: int, items: list) -> list:
        """Согласование строк server_gallery (контракт H-6/M7-03) — через
        общий параметризованный помощник gallery_ops (учтён в общем лимите
        500 МБ — см. gallery_ops._gallery_total_all)."""
        return self._save_gallery_rows_generic(
            "server_gallery", "server_id", server_id, items)

    # ----- Связи сервер ↔ аккаунт -----

    def get_server_links(self, server_id: int) -> list[int]:
        """id аккаунтов, связанных с сервером."""
        self.cursor.execute(
            "SELECT account_id FROM server_links WHERE server_id = ?", (server_id,))
        return [r["account_id"] for r in self.cursor.fetchall()]

    def get_account_server_links(self, account_id: int) -> list[dict[str, Any]]:
        """Серверы, связанные с аккаунтом (без серверов в корзине).
        Каждый: id, name, paid_until."""
        self.cursor.execute(
            "SELECT s.id, s.name, s.paid_until "
            "FROM server_links sl JOIN servers s ON s.id = sl.server_id "
            "WHERE sl.account_id = ? AND s.deleted_at IS NULL "
            "ORDER BY s.name COLLATE NOCASE", (account_id,))
        return [{"id": r["id"], "name": r["name"], "paid_until": r["paid_until"]}
                for r in self.cursor.fetchall()]

    def get_server_link_accounts(self, server_id: int) -> list[dict[str, Any]]:
        """Живые аккаунты, связанные с сервером: id, name (путь). Аккаунты в
        корзине не показываются (их связи в БД сохраняются до restore, §8)."""
        ids = self.get_server_links(server_id)
        if not ids:
            return []
        acc, svc, fld = self._name_maps()
        result = [{"id": i, "name": self._path_from_maps(i, acc, svc, fld)}
                  for i in ids if acc.get(i) and not acc[i][2]]
        result.sort(key=lambda r: str(r["name"]).lower())
        return result

    def list_servers(self) -> list[dict[str, Any]]:
        """Живые серверы (для диалога «+ ПРИВЯЗАТЬ» на карточке аккаунта):
        id, name, paid_until."""
        self.cursor.execute(
            "SELECT id, name, paid_until FROM servers "
            "WHERE deleted_at IS NULL ORDER BY name COLLATE NOCASE")
        return [{"id": r["id"], "name": r["name"], "paid_until": r["paid_until"]}
                for r in self.cursor.fetchall()]

    def _set_server_links_rows(self, server_id: int,
                               account_ids: list[int]) -> None:
        """Тело set_server_links без транзакции/dirty (перезапись DELETE+
        INSERT). Связи с аккаунтами в корзине не трогаются: UI их не
        показывает, и перезапись видимого списка не должна молча рвать их
        (restore вернёт, §8)."""
        self.cursor.execute(
            "DELETE FROM server_links WHERE server_id = ? AND account_id IN "
            "(SELECT id FROM accounts WHERE deleted_at IS NULL)", (server_id,))
        seen: set[int] = set()
        for aid in account_ids:
            if aid in seen:                 # дедуп входного списка
                continue
            seen.add(aid)
            self.cursor.execute(
                "INSERT OR IGNORE INTO server_links (server_id, account_id) "
                "VALUES (?, ?)", (server_id, aid))

    def set_server_links(self, server_id: int, account_ids: list[int]) -> None:
        """Задаёт связи сервера с живыми аккаунтами (старые связи заменяются;
        связи с аккаунтами в корзине сохраняются)."""
        with self.conn:
            self._set_server_links_rows(server_id, account_ids)
        self._mark_dirty()

    def _set_account_server_links_rows(self, account_id: int,
                                       server_ids: list[int]) -> None:
        """Перезапись связей со стороны аккаунта (сохранение его карточки).
        Зеркало _set_server_links_rows: связи с серверами в корзине не
        трогаются — restore сервера вернёт связь (§8)."""
        self.cursor.execute(
            "DELETE FROM server_links WHERE account_id = ? AND server_id IN "
            "(SELECT id FROM servers WHERE deleted_at IS NULL)", (account_id,))
        seen: set[int] = set()
        for sid in server_ids:
            if sid in seen:                 # дедуп входного списка
                continue
            seen.add(sid)
            self.cursor.execute(
                "INSERT OR IGNORE INTO server_links (server_id, account_id) "
                "VALUES (?, ?)", (sid, account_id))

    # ----- Переименование -----

    def rename_server(self, server_id: int, name: str) -> None:
        """Переименовывает сервер (по образцу rename_service/rename_folder)."""
        self._write("UPDATE servers SET name = ? WHERE id = ?", (name, server_id))

    # ----- Корзина (мягкое удаление) -----

    def _move_server_to_bin_rows(self, server_id: int) -> None:
        """Тело move_server_to_bin без транзакции/dirty (для составных
        методов, по образцу _move_fin_item_to_bin_rows)."""
        self.cursor.execute(
            "UPDATE servers SET deleted_at = ? WHERE id = ?",
            (datetime.now().isoformat(" ", "seconds"), server_id))

    def _delete_server_rows(self, server_id: int) -> None:
        """Тело безвозвратного удаления без транзакции/dirty (для составных
        методов)."""
        self.cursor.execute("DELETE FROM servers WHERE id = ?", (server_id,))

    def move_server_to_bin(self, server_id: int) -> None:
        """Переносит сервер в корзину (мягкое удаление)."""
        self._write(
            "UPDATE servers SET deleted_at = ? WHERE id = ?",
            (datetime.now().isoformat(" ", "seconds"), server_id))

    def restore_server(self, server_id: int) -> None:
        """Восстанавливает сервер из корзины."""
        self._write(
            "UPDATE servers SET deleted_at = NULL WHERE id = ?", (server_id,))

    def delete_server_forever(self, server_id: int) -> None:
        """Безвозвратно удаляет сервер по id (корзина, «Удалить навсегда»).
        FK ON DELETE CASCADE каскадно чистит server_links/server_gallery.
        Симметрична restore_server."""
        with self.conn:
            self._delete_server_rows(server_id)
        self._invalidate_gallery_bytes()   # каскад мог удалить картинки (M-9)
        self._mark_dirty()

    def get_deleted_servers(self) -> list[dict[str, Any]]:
        """Список серверов в корзине (последние удалённые — сверху). Каждый:
        id, name, deleted_at."""
        self.cursor.execute(
            "SELECT id, name, deleted_at FROM servers "
            "WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC, id DESC")
        return [{"id": r["id"], "name": r["name"], "deleted_at": r["deleted_at"]}
                for r in self.cursor.fetchall()]

    # ----- Перемещение, избранное, порядок -----

    def _move_server_rows(self, server_id: int,
                          service_id: Optional[int]) -> None:
        """Тело move_server без транзакции/dirty (в конец списка сервиса)."""
        order = self._next_sort_order("servers", "service_id", service_id)
        self.cursor.execute(
            "UPDATE servers SET service_id = ?, sort_order = ? WHERE id = ?",
            (service_id, order, server_id))

    def move_server(self, server_id: int, service_id: Optional[int]) -> None:
        """Переносит сервер в сервис (service_id=None — свободный), в конец
        списка."""
        with self.conn:
            self._move_server_rows(server_id, service_id)
        self._mark_dirty()

    def move_servers(self, server_ids: list[int],
                     service_id: Optional[int]) -> None:
        """Пакетно переносит серверы в сервис в ОДНОЙ транзакции (по образцу
        move_fin_items): сбой середины списка не оставляет часть серверов
        перемещённой, а часть — нет."""
        with self.conn:
            for sid in server_ids:
                self._move_server_rows(sid, service_id)
        self._mark_dirty()

    def _set_server_favorite_rows(self, server_id: int, value: bool) -> None:
        """Тело set_server_favorite без транзакции/dirty."""
        self.cursor.execute(
            "UPDATE servers SET is_favorite = ? WHERE id = ?",
            (1 if value else 0, server_id))

    def set_server_favorite(self, server_id: int, value: bool) -> None:
        """Проставляет/снимает избранное сервера."""
        with self.conn:
            self._set_server_favorite_rows(server_id, value)
        self._mark_dirty()

    def set_server_favorites(self, server_ids: list[int], value: bool) -> None:
        """Пакетно проставляет/снимает избранное в ОДНОЙ транзакции (по
        образцу set_fin_favorites)."""
        with self.conn:
            for sid in server_ids:
                self._set_server_favorite_rows(sid, value)
        self._mark_dirty()

    def set_servers_order(self, ordered_ids: list[int]) -> None:
        """Записывает sort_order серверам в порядке переданного списка id
        (DnD)."""
        with self.conn:
            for i, sid in enumerate(ordered_ids):
                self.cursor.execute(
                    "UPDATE servers SET sort_order = ? WHERE id = ?", (i, sid))
        self._mark_dirty()
