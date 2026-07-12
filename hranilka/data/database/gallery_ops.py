"""Галерея: BLOB-картинки аккаунтов, кэш суммарного объёма (M-9), ленивое
чтение и согласование строк при сохранении (контракт H-6). Часть класса
Database (database.py) — методы вынесены дословно (backlog-разрез CRUD)."""
import sqlite3
from hranilka.data.database.state import DbBase

# Единый инвариант для gallery и fin_gallery. UI использует тот же предел для
# раннего сообщения, но проверка здесь окончательная: осиротевший импорт или
# другой вызов слоя данных не могут обойти ограничение.
GALLERY_TOTAL_BYTES_LIMIT = 500 * 1024 * 1024


class DbGalleryOpsMixin(DbBase):
    def _invalidate_gallery_bytes(self):
        """Сбросить кэш суммарного объёма галереи (после любой её мутации)."""
        self._gallery_bytes = None

    def _gallery_total_all(self) -> int:
        """Полный SUM(LENGTH(image_data)) обеих галерей с мемоизацией (M-9).

        Значение считается один раз и хранится в self._gallery_bytes до первой
        мутации галереи или смены сессии (там кэш сбрасывается в None)."""
        if self._gallery_bytes is None:
            self.cursor.execute(
                "SELECT "
                "COALESCE((SELECT SUM(LENGTH(image_data)) FROM gallery), 0) + "
                "COALESCE((SELECT SUM(LENGTH(image_data)) FROM fin_gallery), 0) "
                "AS s")
            self._gallery_bytes = int(self.cursor.fetchone()["s"])
        return self._gallery_bytes

    def gallery_total_bytes(self, exclude_account_id: int | None = None,
                            exclude_fin_item_id: int | None = None) -> int:
        """Суммарный объём всех картинок в обеих галереях (в байтах).

        exclude_account_id — исключить указанный аккаунт из суммы: его картинки
        обычно держатся в памяти редактируемой карточки, и учитывать их повторно
        при проверке лимита суммарного объёма не нужно (M3-05).

        Полный объём кэшируется (M-9); при exclude вычитаем объём редактируемого
        аккаунта и/или финансовой записи из кэшированной суммы."""
        total = self._gallery_total_all()
        if exclude_account_id is not None:
            self.cursor.execute(
                "SELECT COALESCE(SUM(LENGTH(image_data)), 0) AS s "
                "FROM gallery WHERE account_id = ?", (exclude_account_id,))
            total -= int(self.cursor.fetchone()["s"])
        if exclude_fin_item_id is not None:
            self.cursor.execute(
                "SELECT COALESCE(SUM(LENGTH(image_data)), 0) AS s "
                "FROM fin_gallery WHERE item_id = ?", (exclude_fin_item_id,))
            total -= int(self.cursor.fetchone()["s"])
        return total


    def _load_gallery_image(self, table: str, image_id: int):
        """Загружает BLOB одной строки галереи из указанной таблицы (gallery или
        fin_gallery). table — константа кода, не пользовательский ввод."""
        self.cursor.execute(
            f"SELECT image_data FROM {table} WHERE id = ?", (image_id,))
        row = self.cursor.fetchone()
        if row is None or row["image_data"] is None:
            return None
        return bytes(row["image_data"])

    def load_gallery_image(self, image_id: int):
        """Загружает BLOB одного изображения галереи по его id.
        Используется для ленивой загрузки: при load_account image_data не читается,
        а запрашивается отдельно только когда виджет хочет показать миниатюру."""
        return self._load_gallery_image("gallery", image_id)


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
        return self._save_gallery_rows_generic(
            "gallery", "account_id", account_id, items)

    def _save_gallery_rows_generic(self, table, fk_col, fk_id, items):
        """Согласование строк галереи (контракт H-6) для любой таблицы-галереи:
        gallery(account_id) и fin_gallery(item_id). table/fk_col — константы
        кода, не пользовательский ввод. Правила и формат результата — как в
        _save_gallery_rows (см. её докстринг)."""
        # id всех текущих строк галереи владельца — чтобы удалить отсутствующие
        # в новом списке и валидировать переданные image_id.
        self.cursor.execute(
            f"SELECT id, LENGTH(image_data) AS size FROM {table} WHERE {fk_col} = ?",
            (fk_id,))
        existing_sizes = {r["id"]: int(r["size"] or 0)
                          for r in self.cursor.fetchall()}
        existing_ids = set(existing_sizes)

        self._ensure_gallery_limit(existing_sizes, items)

        kept_ids = {g["image_id"] for g in items
                    if g.get("image_id") is not None and g["image_id"] in existing_ids}
        # Явно удалённые пользователем: были в БД, но их id нет в переданном списке.
        to_delete = existing_ids - kept_ids
        for row_id in to_delete:
            self.cursor.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))

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
                        f"UPDATE {table} SET description = ? WHERE id = ?",
                        (desc, image_id))
                    saved_ids.append(image_id)
                else:
                    saved_ids.append(None)
                continue
            blob = sqlite3.Binary(data)
            if image_id is not None and image_id in existing_ids:
                # Обновление BLOB существующей строки (перезалитая картинка).
                self.cursor.execute(
                    f"UPDATE {table} SET description = ?, image_data = ? WHERE id = ?",
                    (desc, blob, image_id))
                saved_ids.append(image_id)
            else:
                # Новая картинка — вставляем в конец (новый, больший id).
                self.cursor.execute(
                    f"INSERT INTO {table} ({fk_col}, description, image_data) "
                    f"VALUES (?, ?, ?)", (fk_id, desc, blob))
                saved_ids.append(int(self.cursor.lastrowid))
        # Обе таблицы участвуют в едином кэше. Инвалидируем только после
        # успешной мутации: при откате транзакции кэш по-прежнему корректен.
        self._invalidate_gallery_bytes()
        return saved_ids

    def _ensure_gallery_limit(self, existing_sizes, items):
        """Проверить итоговый размер БД после замены галереи одного владельца.

        ``items`` описывает финальное состояние текущей галереи. Считаем его до
        DELETE/UPDATE/INSERT, поэтому ошибка не оставляет частично сохранённых
        BLOB. Эта проверка защищает и обычный UI-путь, и осиротевшие загрузки.
        """
        existing_ids = set(existing_sizes)
        kept_ids = {g.get("image_id") for g in items
                    if g.get("image_id") in existing_ids}
        replacement_sizes = {}
        new_bytes = 0
        for g in items:
            data = g.get("data")
            image_id = g.get("image_id")
            if data is None:
                continue
            size = len(data)
            if image_id in existing_ids:
                replacement_sizes[image_id] = size
            else:
                new_bytes += size
        final_owner_bytes = sum(
            replacement_sizes.get(image_id, existing_sizes[image_id])
            for image_id in kept_ids)
        projected = (self._gallery_total_all() - sum(existing_sizes.values())
                     + final_owner_bytes + new_bytes)
        if projected > GALLERY_TOTAL_BYTES_LIMIT:
            raise ValueError(
                "Превышен общий лимит галерей: 500 МБ. Удалите изображения "
                "или выберите файл меньшего размера.")

    # ----- Сбор данных для экспорта -----

