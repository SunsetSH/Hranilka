"""Финансовые записи (карты/кошельки): CRUD карточки, связи элемент↔аккаунт,
галерея, корзина, избранное, сортировка. Часть класса Database (database.py).

Паттерн — как в accounts.py: публичный метод = транзакция (`with self.conn`)
+ self._mark_dirty(); приватный `_..._rows` = чистый SQL для переиспользования
под общей транзакцией. Экстракт-колонки card_last4/expires_on пересчитываются
из payload при каждом сохранении через ItemTypeSpec.extract (единственный
писатель — рассинхронизация с JSON исключена по построению).
"""
import json
from datetime import date, datetime
from typing import Any, Optional

from hranilka.core.fin_types import FIN_TYPES
from hranilka.data.database.state import DbBase


class DbFinItemsMixin(DbBase):
    # ----- Создание -----

    def _add_fin_item_rows(self, service_id: Optional[int], item_type: str,
                           name: str) -> int:
        """Тело add_fin_item без транзакции/dirty (для составных методов).
        created_at — строкой формата SQLite CURRENT_TIMESTAMP (как в accounts)."""
        self.cursor.execute(
            "INSERT INTO fin_items (service_id, item_type, name, data, created_at) "
            "VALUES (?, ?, ?, '{}', ?)",
            (service_id, item_type, name,
             datetime.now().isoformat(" ", "seconds")))
        return int(self.cursor.lastrowid)

    def add_fin_item(self, service_id: Optional[int], item_type: str,
                     name: str) -> int:
        """Создаёт пустую финансовую запись, возвращает её id."""
        with self.conn:
            new_id = self._add_fin_item_rows(service_id, item_type, name)
        self._mark_dirty()
        return new_id

    def count_fin_items(self) -> int:
        """Всего финансовых записей в БД, включая корзину (для предупреждения
        при выключении показа фин-инструментов). Без фильтра deleted_at."""
        self.cursor.execute("SELECT COUNT(*) AS n FROM fin_items")
        return int(self.cursor.fetchone()["n"])

    # ----- Загрузка/сохранение карточки -----

    def load_fin_item(self, item_id: int) -> Optional[dict[str, Any]]:
        """Полная карточка записи в формате storage (см. models.FinItemData),
        или None, если запись не найдена. BLOB галереи не читается — ленивая
        загрузка (контракт H-6): вернётся data=None + image_id + blob_size."""
        self.cursor.execute("SELECT * FROM fin_items WHERE id = ?", (item_id,))
        row = self.cursor.fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["data"]) if row["data"] else {}
        except (ValueError, TypeError):
            payload = {}

        self.cursor.execute(
            "SELECT id, description, LENGTH(image_data) AS blob_size "
            "FROM fin_gallery WHERE item_id = ? ORDER BY id", (item_id,))
        gallery = [
            {"id": r["id"], "image_id": r["id"], "desc": r["description"] or "",
             "data": None, "blob_size": r["blob_size"]}
            for r in self.cursor.fetchall()
        ]
        return {"item_type": row["item_type"], "name": row["name"],
                "payload": payload, "gallery": gallery}

    @staticmethod
    def _fin_extract(item_type: str,
                     payload: dict) -> tuple[Optional[str], Optional[str]]:
        """(card_last4, expires_on-строка) из дескриптора типа для экстракт-колонок.
        expires_on приводится к строке «ГГГГ-ММ-ДД» (date как SQL-параметр даёт
        DeprecationWarning на 3.12+). Неизвестный тип → (None, None)."""
        spec = FIN_TYPES.get(item_type)
        if spec is None:
            return (None, None)
        last4, expires_on = spec.extract(payload)
        exp_str = expires_on.isoformat() if isinstance(expires_on, date) \
            else (expires_on or None)
        return (last4 or None, exp_str)

    def _save_fin_item_rows(self, item_id: int,
                            storage: dict[str, Any]) -> list:
        """Тело save_fin_item без транзакции/dirty. Пишет name/data и
        пересчитанные экстракт-колонки, затем согласует галерею. item_type в БД
        не меняется (фиксирован при создании) — для extract берём его из storage
        (он пришёл из load_fin_item того же элемента)."""
        item_type = storage.get("item_type") or ""
        name = storage.get("name") or ""
        payload = storage.get("payload") or {}
        card_last4, expires_on = self._fin_extract(item_type, payload)
        data_json = json.dumps(payload, ensure_ascii=False)
        self.cursor.execute(
            "UPDATE fin_items SET name = ?, data = ?, card_last4 = ?, "
            "expires_on = ? WHERE id = ?",
            (name, data_json, card_last4, expires_on, item_id))
        # Запись могла быть удалена между открытием и сохранением: если UPDATE
        # не затронул строку — прерываем внутри транзакции (откат галереи, L-6).
        if self.cursor.rowcount != 1:
            raise ValueError("Финансовая запись не найдена")
        return self._save_fin_gallery_rows(item_id, storage.get("gallery") or [])

    def save_fin_item(self, item_id: int, storage: dict[str, Any]) -> list:
        """Сохраняет карточку записи. Возвращает id строк галереи (как
        save_account — для присвоения id новым картинкам)."""
        with self.conn:
            gallery_ids = self._save_fin_item_rows(item_id, storage)
        self._mark_dirty()
        return gallery_ids

    def save_fin_item_with_links(self, item_id: int, storage: dict[str, Any],
                                 account_ids: list[int]) -> list:
        """Атомарно сохраняет карточку и её связи в ОДНОЙ транзакции (по образцу
        save_account_with_links): либо обе, либо ни одна."""
        with self.conn:
            gallery_ids = self._save_fin_item_rows(item_id, storage)
            self._set_item_links_rows(item_id, account_ids)
        self._mark_dirty()
        return gallery_ids

    # ----- Галерея (тонкие обёртки над общей логикой gallery_ops) -----

    def load_fin_gallery_image(self, image_id: int):
        """BLOB одной строки fin_gallery (ленивая загрузка миниатюры)."""
        return self._load_gallery_image("fin_gallery", image_id)

    def _save_fin_gallery_rows(self, item_id: int, items: list) -> list:
        """Согласование строк fin_gallery (контракт H-6/M7-03) — через общий
        параметризованный помощник gallery_ops."""
        return self._save_gallery_rows_generic(
            "fin_gallery", "item_id", item_id, items)

    # ----- Связи элемент ↔ аккаунт -----

    def get_item_links(self, item_id: int) -> list[int]:
        """id аккаунтов, связанных с финансовой записью."""
        self.cursor.execute(
            "SELECT account_id FROM fin_links WHERE item_id = ?", (item_id,))
        return [r["account_id"] for r in self.cursor.fetchall()]

    def get_account_fin_links(self, account_id: int) -> list[dict[str, Any]]:
        """Финансовые записи, связанные с аккаунтом (без записей в корзине).
        Каждая: id, item_type, name, card_last4."""
        self.cursor.execute(
            "SELECT fi.id, fi.item_type, fi.name, fi.card_last4 "
            "FROM fin_links fl JOIN fin_items fi ON fi.id = fl.item_id "
            "WHERE fl.account_id = ? AND fi.deleted_at IS NULL "
            "ORDER BY fi.name COLLATE NOCASE", (account_id,))
        return [{"id": r["id"], "item_type": r["item_type"], "name": r["name"],
                 "card_last4": r["card_last4"]} for r in self.cursor.fetchall()]

    def get_item_link_accounts(self, item_id: int) -> list[dict[str, Any]]:
        """Живые аккаунты, связанные с записью: id, name (путь). Аккаунты в
        корзине не показываются (их связи в БД сохраняются до restore, §8)."""
        ids = self.get_item_links(item_id)
        if not ids:
            return []
        acc, svc, fld = self._name_maps()
        result = [{"id": i, "name": self._path_from_maps(i, acc, svc, fld)}
                  for i in ids if acc.get(i) and not acc[i][2]]
        result.sort(key=lambda r: str(r["name"]).lower())
        return result

    def list_fin_items(self) -> list[dict[str, Any]]:
        """Живые финансовые записи (для диалога «+ ПРИВЯЗАТЬ» на карточке
        аккаунта): id, item_type, name, card_last4."""
        self.cursor.execute(
            "SELECT id, item_type, name, card_last4 FROM fin_items "
            "WHERE deleted_at IS NULL ORDER BY name COLLATE NOCASE")
        return [{"id": r["id"], "item_type": r["item_type"], "name": r["name"],
                 "card_last4": r["card_last4"]} for r in self.cursor.fetchall()]

    def _set_item_links_rows(self, item_id: int, account_ids: list[int]) -> None:
        """Тело set_item_links без транзакции/dirty (перезапись DELETE+INSERT).
        Связи с аккаунтами в корзине не трогаются: UI их не показывает, и
        перезапись видимого списка не должна молча рвать их (restore вернёт, §8)."""
        self.cursor.execute(
            "DELETE FROM fin_links WHERE item_id = ? AND account_id IN "
            "(SELECT id FROM accounts WHERE deleted_at IS NULL)", (item_id,))
        seen: set[int] = set()
        for aid in account_ids:
            if aid in seen:                 # дедуп входного списка
                continue
            seen.add(aid)
            self.cursor.execute(
                "INSERT OR IGNORE INTO fin_links (item_id, account_id) "
                "VALUES (?, ?)", (item_id, aid))

    def set_item_links(self, item_id: int, account_ids: list[int]) -> None:
        """Задаёт связи записи с живыми аккаунтами (старые связи заменяются;
        связи с аккаунтами в корзине сохраняются)."""
        with self.conn:
            self._set_item_links_rows(item_id, account_ids)
        self._mark_dirty()

    def _set_account_fin_links_rows(self, account_id: int,
                                    item_ids: list[int]) -> None:
        """Перезапись связей со стороны аккаунта (сохранение его карточки).
        Зеркало _set_item_links_rows: связи с записями в корзине не трогаются —
        restore записи вернёт связь (§8)."""
        self.cursor.execute(
            "DELETE FROM fin_links WHERE account_id = ? AND item_id IN "
            "(SELECT id FROM fin_items WHERE deleted_at IS NULL)", (account_id,))
        seen: set[int] = set()
        for iid in item_ids:
            if iid in seen:                 # дедуп входного списка
                continue
            seen.add(iid)
            self.cursor.execute(
                "INSERT OR IGNORE INTO fin_links (item_id, account_id) "
                "VALUES (?, ?)", (iid, account_id))

    # ----- Корзина (мягкое удаление) -----

    def _move_fin_item_to_bin_rows(self, item_id: int) -> None:
        """Тело move_fin_item_to_bin без транзакции/dirty (для delete_items)."""
        self.cursor.execute(
            "UPDATE fin_items SET deleted_at = ? WHERE id = ?",
            (datetime.now().isoformat(" ", "seconds"), item_id))

    def _delete_fin_item_rows(self, item_id: int) -> None:
        """Тело безвозвратного удаления без транзакции/dirty (для delete_items)."""
        self.cursor.execute("DELETE FROM fin_items WHERE id = ?", (item_id,))

    def move_fin_item_to_bin(self, item_id: int) -> None:
        """Переносит запись в корзину (мягкое удаление)."""
        self._write(
            "UPDATE fin_items SET deleted_at = ? WHERE id = ?",
            (datetime.now().isoformat(" ", "seconds"), item_id))

    def restore_fin_item(self, item_id: int) -> None:
        """Восстанавливает запись из корзины."""
        self._write(
            "UPDATE fin_items SET deleted_at = NULL WHERE id = ?", (item_id,))

    def delete_fin_item_forever(self, item_id: int) -> None:
        """Безвозвратно удаляет запись по id (корзина, «Удалить навсегда»).

        Не зависит от item_type/реестра — в отличие от delete_items (который
        роутит по node_type дерева), эта запись может быть в корзине уже
        удалённого из реестра типа. Симметрична restore_fin_item."""
        with self.conn:
            self._delete_fin_item_rows(item_id)
        self._invalidate_gallery_bytes()   # каскад мог удалить картинки (M-9)
        self._mark_dirty()

    # ----- Перемещение, избранное, порядок -----

    def _move_fin_item_rows(self, item_id: int,
                            service_id: Optional[int]) -> None:
        """Тело move_fin_item без транзакции/dirty (в конец списка сервиса)."""
        order = self._next_sort_order("fin_items", "service_id", service_id)
        self.cursor.execute(
            "UPDATE fin_items SET service_id = ?, sort_order = ? WHERE id = ?",
            (service_id, order, item_id))

    def move_fin_item(self, item_id: int, service_id: Optional[int]) -> None:
        """Переносит запись в сервис (service_id=None — свободная), в конец списка."""
        with self.conn:
            self._move_fin_item_rows(item_id, service_id)
        self._mark_dirty()

    def move_fin_items(self, item_ids: list[int],
                       service_id: Optional[int]) -> None:
        """Пакетно переносит записи в сервис в ОДНОЙ транзакции (M7-04, по
        образцу move_accounts): сбой середины списка не оставляет часть
        записей перемещённой, а часть — нет."""
        with self.conn:
            for iid in item_ids:
                self._move_fin_item_rows(iid, service_id)
        self._mark_dirty()

    def _set_fin_favorite_rows(self, item_id: int, value: bool) -> None:
        """Тело set_fin_favorite без транзакции/dirty."""
        self.cursor.execute(
            "UPDATE fin_items SET is_favorite = ? WHERE id = ?",
            (1 if value else 0, item_id))

    def set_fin_favorite(self, item_id: int, value: bool) -> None:
        """Проставляет/снимает избранное записи."""
        with self.conn:
            self._set_fin_favorite_rows(item_id, value)
        self._mark_dirty()

    def set_fin_favorites(self, item_ids: list[int], value: bool) -> None:
        """Пакетно проставляет/снимает избранное в ОДНОЙ транзакции (M7-04,
        по образцу set_favorites)."""
        with self.conn:
            for iid in item_ids:
                self._set_fin_favorite_rows(iid, value)
        self._mark_dirty()

    def set_fin_items_order(self, ordered_ids: list[int]) -> None:
        """Записывает sort_order записям в порядке переданного списка id (DnD)."""
        with self.conn:
            for i, iid in enumerate(ordered_ids):
                self.cursor.execute(
                    "UPDATE fin_items SET sort_order = ? WHERE id = ?", (i, iid))
        self._mark_dirty()
