"""Карточка аккаунта: загрузка/сохранение полной карточки и связи между
аккаунтами. Часть класса Database (database.py) — методы вынесены
дословно (backlog-разрез CRUD)."""
from typing import Any
from hranilka.core.domain import canonical_link_pair
from hranilka.data.database.state import DbBase


class DbAccountCardMixin(DbBase):
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

    def save_account_with_links(self, account_id, data, target_ids,
                                fin_item_ids=None):
        """Атомарно сохраняет карточку и её связи В ОДНОЙ транзакции (H6-02):
        раньше save_account и set_links были двумя транзакциями — сбой второй
        оставлял карточку записанной, а связи нет. Теперь либо обе, либо ни одна.
        fin_item_ids — привязанные карты/кошельки (концепт §8): перезаписываются
        той же транзакцией; None — связи fin_links не трогаются (старые вызовы).
        Возвращает список id строк галереи (как save_account)."""
        with self.conn:
            gallery_ids = self._save_account_rows(account_id, data)
            self._set_links_rows(account_id, target_ids)
            if fin_item_ids is not None:
                self._set_account_fin_links_rows(account_id, fin_item_ids)
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
