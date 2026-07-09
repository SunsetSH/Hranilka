"""Галерея: BLOB-картинки аккаунтов, кэш суммарного объёма (M-9), ленивое
чтение и согласование строк при сохранении (контракт H-6). Часть класса
Database (database.py) — методы вынесены дословно (backlog-разрез CRUD)."""
import sqlite3
from hranilka.data.database.state import DbBase


class DbGalleryOpsMixin(DbBase):
    def _invalidate_gallery_bytes(self):
        """Сбросить кэш суммарного объёма галереи (после любой её мутации)."""
        self._gallery_bytes = None

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


    def load_gallery_image(self, image_id: int):
        """Загружает BLOB одного изображения галереи по его id.
        Используется для ленивой загрузки: при load_account image_data не читается,
        а запрашивается отдельно только когда виджет хочет показать миниатюру."""
        self.cursor.execute("SELECT image_data FROM gallery WHERE id = ?", (image_id,))
        row = self.cursor.fetchone()
        if row is None or row["image_data"] is None:
            return None
        return bytes(row["image_data"])


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

