"""Bulk-выгрузки: экспорт поддерева и карты имён/путей без N+1 (3 запроса
вместо 1+3N). Часть класса Database (database.py) — методы вынесены
дословно (backlog-разрез CRUD)."""
import json
from typing import Any
from hranilka.core.nodetypes import is_fin_node, is_server_node
from hranilka.data.database.state import DbBase


class DbBulkOpsMixin(DbBase):
    def export_subtree(self, node_type=None, node_id=None, *,
                       include_gallery=True, include_fin=True, include_servers=True,
                       gallery_ids_only=False):
        """Собирает дерево с полными карточками аккаунтов для экспорта.

        node_type=None — вся база. Иначе возвращается только ветка указанного
        узла (папка/сервис/аккаунт). У каждого узла type=='account' добавлены
        ключи 'card' (как load_account) и 'links' (как get_links). Финансовым
        листьям (card/wallet) добавлены ключи 'fin' (item_type/name/payload/
        gallery) и 'links' (связанные живые аккаунты). Листьям-серверам —
        ключи 'server' (name/payload/gallery, зеркало 'fin') и 'links'
        (связанные живые аккаунты провайдера); ветка полностью независима от
        fin-предзагрузки (docs/ТЗ_VPS_Серверы.md §2). Метод только читает БД
        (не помечает её грязной).

        include_gallery=False — BLOB-ы галерей (account/fin/server) вовсе не
        читаются из БД (M-04: раньше снимок читал все картинки ещё до того,
        как пользователь мог снять галку «Галерея» в диалоге экспорта — на
        базе с гигабайтами вложений это многосекундное зависание и пиковая
        память). include_fin=False/include_servers=False — записи
        соответствующего типа не читаются вовсе (ни payload, ни их галерея),
        а не просто отфильтровываются на этапе форматирования, как раньше.

        gallery_ids_only=True (действует только при include_gallery=True) —
        BLOB-ы галерей НЕ читаются, но сами строки читаются: каждый элемент
        galley — {"desc", "data": None, "image_id"} вместо {"desc", "data":
        bytes}. Строки без BLOB (image_data IS NULL) в выборку не попадают —
        как и раньше, у них «нет картинки». Используется потоковым HTML-
        экспортом (services/export.py): снимок остаётся лёгким (без 1.33×
        base64-амплификации на каждую картинку сразу для всей галереи), а
        сами байты читаются по одной картинке через отдельный провайдер
        (M-04, второй проход ревью — см. docs/CODE_REVIEW_VPS_SERVERS_2026-07-15.md)."""
        # ручной порядок, без корзины; include_servers=True у get_tree_structure —
        # структура дерева всегда содержит fin/server-узлы (иначе имена веток
        # пропадали бы), фильтрация форматирования — через opts.include_* в
        # services/export.py; здесь include_fin/include_servers управляют только
        # тем, читаются ли САМИ карточки (payload/BLOB) этих узлов из БД.
        full = self.get_tree_structure(include_servers=True)
        if node_type is None:
            roots = full
        else:
            found = self._find_node(full, node_type, node_id)
            roots = [found] if found else []

        return self._export_roots(
            roots, include_gallery=include_gallery, include_fin=include_fin,
            include_servers=include_servers, gallery_ids_only=gallery_ids_only)

    def export_selected(self, selected_nodes, *, include_gallery=True,
                        include_fin=True, include_servers=True,
                        gallery_ids_only=False):
        """Собрать независимые ветви выделенных пользователем узлов.

        Если выделены и контейнер, и его потомок, потомок уже входит в экспорт
        контейнера и второй раз не добавляется. Порядок соответствует дереву,
        а неизвестные/удалённые к моменту снимка узлы безопасно игнорируются.
        """
        wanted = {(node_type, node_id) for node_type, node_id in selected_nodes}
        if not wanted:
            return []
        full = self.get_tree_structure(include_servers=True)
        roots = []

        def visit(node, ancestor_selected=False):
            key = (node["type"], node["id"])
            selected = key in wanted
            if selected and not ancestor_selected:
                roots.append(node)
            for child in node.get("children", []):
                visit(child, ancestor_selected or selected)

        for root in full:
            visit(root)
        return self._export_roots(
            roots, include_gallery=include_gallery, include_fin=include_fin,
            include_servers=include_servers, gallery_ids_only=gallery_ids_only)

    def _export_roots(self, roots, *, include_gallery, include_fin,
                      include_servers, gallery_ids_only):
        """Догрузить карточки для уже выбранных корней дерева."""

        # Bulk-предзагрузка вместо load_account()/get_links() на каждый аккаунт
        # (устранение N+1: раньше экспорт 100 аккаунтов делал ~728 SELECT).
        ids: list[int] = []
        fin_ids: list[int] = []
        server_ids: list[int] = []
        for root in roots:
            self._collect_account_ids(root, ids)
            if include_fin:
                self._collect_fin_item_ids(root, fin_ids)
            if include_servers:
                self._collect_server_ids(root, server_ids)
        cards = self._load_cards_bulk(ids, load_gallery=include_gallery,
                                      ids_only=gallery_ids_only)
        links = self._load_links_bulk(ids)
        if include_fin:
            fin_items = self._load_fin_items_bulk(fin_ids, load_gallery=include_gallery,
                                                  ids_only=gallery_ids_only)
            fin_links = self._load_fin_links_bulk(fin_ids)
        else:
            fin_items, fin_links = {}, {}
        if include_servers:
            servers = self._load_servers_bulk(server_ids, load_gallery=include_gallery,
                                              ids_only=gallery_ids_only)
            server_links = self._load_server_links_bulk(server_ids)
        else:
            servers, server_links = {}, {}
        for root in roots:
            self._attach_cards_preloaded(root, cards, links)
            if include_fin:
                self._attach_fin_preloaded(root, fin_items, fin_links)
            if include_servers:
                self._attach_servers_preloaded(root, servers, server_links)
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

    def _collect_fin_item_ids(self, node, out):
        """Собирает id всех финансовых записей (card/wallet)."""
        if is_fin_node(node["type"]):
            out.append(node["id"])
        for child in node.get("children", []):
            self._collect_fin_item_ids(child, out)

    def _attach_fin_preloaded(self, node, fin_items, fin_links):
        """Вкладывает предзагруженную карточку записи и связи в fin-листья."""
        if is_fin_node(node["type"]):
            node["fin"] = fin_items.get(node["id"])
            node["links"] = fin_links.get(node["id"], [])
        for child in node.get("children", []):
            self._attach_fin_preloaded(child, fin_items, fin_links)

    def _collect_server_ids(self, node, out):
        """Собирает id всех серверов в ветке (рекурсивно, зеркало
        _collect_fin_item_ids)."""
        if is_server_node(node["type"]):
            out.append(node["id"])
        for child in node.get("children", []):
            self._collect_server_ids(child, out)

    def _attach_servers_preloaded(self, node, servers, server_links):
        """Вкладывает предзагруженную карточку сервера и связи в server-листья
        (зеркало _attach_fin_preloaded, независимая ветка)."""
        if is_server_node(node["type"]):
            node["server"] = servers.get(node["id"])
            node["links"] = server_links.get(node["id"], [])
        for child in node.get("children", []):
            self._attach_servers_preloaded(child, servers, server_links)

    @staticmethod
    def _chunks(seq, size=900):
        """Режет список на куски (предел числа параметров в SQLite ~999)."""
        for i in range(0, len(seq), size):
            yield seq[i:i + size]

    def _load_cards_bulk(self, account_ids, load_gallery=True, ids_only=False):
        """{account_id: card} для набора аккаунтов. Card как в load_account().

        load_gallery=False — BLOB-ы галереи не читаются вовсе (c["gallery"]
        остаётся []), экономит и SELECT, и память (M-04).
        ids_only=True (при load_gallery=True) — строки читаются, BLOB нет:
        {"desc", "data": None, "image_id"} (потоковый HTML, второй проход
        M-04)."""
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

            if not load_gallery:
                continue
            if ids_only:
                self.cursor.execute(
                    f"SELECT id, account_id, description FROM gallery "
                    f"WHERE account_id IN ({ph}) AND image_data IS NOT NULL "
                    f"ORDER BY account_id, id", chunk)
                for r in self.cursor.fetchall():
                    c = cards.get(r["account_id"])
                    if c is not None:
                        c["gallery"].append({
                            "desc": r["description"] or "",
                            "data": None, "image_id": r["id"],
                        })
                continue
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

    def _load_fin_items_bulk(self, item_ids, load_gallery=True, ids_only=False):
        """{item_id: {item_type, name, payload, gallery}} без N+1 (один SELECT
        чанками + JSON-парсинг data, зеркало _load_cards_bulk). payload —
        разобранный JSON (битые данные → {}); gallery — описания и BLOB как у
        аккаунтной галереи (для include_gallery в HTML).

        load_gallery=False — BLOB-ы fin_gallery не читаются вовсе (M-04).
        ids_only=True — зеркало _load_cards_bulk (метаданные без BLOB)."""
        items: dict[int, dict[str, Any]] = {}
        if not item_ids:
            return items

        for chunk in self._chunks(item_ids):
            ph = ",".join("?" * len(chunk))
            self.cursor.execute(
                f"SELECT id, item_type, name, data FROM fin_items "
                f"WHERE id IN ({ph})", chunk)
            for r in self.cursor.fetchall():
                try:
                    payload = json.loads(r["data"]) if r["data"] else {}
                except (ValueError, TypeError):
                    payload = {}
                items[r["id"]] = {
                    "item_type": r["item_type"], "name": r["name"],
                    "payload": payload, "gallery": [],
                }

        if not load_gallery:
            return items
        if ids_only:
            for chunk in self._chunks(item_ids):
                ph = ",".join("?" * len(chunk))
                self.cursor.execute(
                    f"SELECT id, item_id, description FROM fin_gallery "
                    f"WHERE item_id IN ({ph}) AND image_data IS NOT NULL "
                    f"ORDER BY item_id, id", chunk)
                for r in self.cursor.fetchall():
                    it = items.get(r["item_id"])
                    if it is not None:
                        it["gallery"].append({
                            "desc": r["description"] or "",
                            "data": None, "image_id": r["id"],
                        })
            return items
        for chunk in self._chunks(item_ids):
            ph = ",".join("?" * len(chunk))
            self.cursor.execute(
                f"SELECT item_id, description, image_data FROM fin_gallery "
                f"WHERE item_id IN ({ph}) ORDER BY item_id, id", chunk)
            for r in self.cursor.fetchall():
                it = items.get(r["item_id"])
                if it is not None:
                    it["gallery"].append({
                        "desc": r["description"] or "",
                        "data": bytes(r["image_data"]) if r["image_data"] is not None else None,
                    })
        return items

    def _load_servers_bulk(self, server_ids, load_gallery=True, ids_only=False):
        """{server_id: {name, payload, gallery}} без N+1 (зеркало
        _load_fin_items_bulk, независимая таблица servers/server_gallery).
        payload — разобранный JSON (битые данные -> {}); gallery — описания и
        BLOB (для include_gallery в HTML).

        load_gallery=False — BLOB-ы server_gallery не читаются вовсе (M-04).
        ids_only=True — зеркало _load_cards_bulk (метаданные без BLOB)."""
        items: dict[int, dict[str, Any]] = {}
        if not server_ids:
            return items

        for chunk in self._chunks(server_ids):
            ph = ",".join("?" * len(chunk))
            self.cursor.execute(
                f"SELECT id, name, data FROM servers WHERE id IN ({ph})", chunk)
            for r in self.cursor.fetchall():
                try:
                    payload = json.loads(r["data"]) if r["data"] else {}
                except (ValueError, TypeError):
                    payload = {}
                items[r["id"]] = {
                    "name": r["name"], "payload": payload, "gallery": [],
                }

        if not load_gallery:
            return items
        if ids_only:
            for chunk in self._chunks(server_ids):
                ph = ",".join("?" * len(chunk))
                self.cursor.execute(
                    f"SELECT id, server_id, description FROM server_gallery "
                    f"WHERE server_id IN ({ph}) AND image_data IS NOT NULL "
                    f"ORDER BY server_id, id", chunk)
                for r in self.cursor.fetchall():
                    it = items.get(r["server_id"])
                    if it is not None:
                        it["gallery"].append({
                            "desc": r["description"] or "",
                            "data": None, "image_id": r["id"],
                        })
            return items
        for chunk in self._chunks(server_ids):
            ph = ",".join("?" * len(chunk))
            self.cursor.execute(
                f"SELECT server_id, description, image_data FROM server_gallery "
                f"WHERE server_id IN ({ph}) ORDER BY server_id, id", chunk)
            for r in self.cursor.fetchall():
                it = items.get(r["server_id"])
                if it is not None:
                    it["gallery"].append({
                        "desc": r["description"] or "",
                        "data": bytes(r["image_data"]) if r["image_data"] is not None else None,
                    })
        return items

    def _load_server_links_bulk(self, server_ids):
        """{server_id: [{"id","name"(путь аккаунта)}]} — связанные живые
        аккаунты провайдера (зеркало _load_fin_links_bulk, таблица
        server_links)."""
        result: dict[int, list[dict[str, Any]]] = {i: [] for i in server_ids}
        if not server_ids:
            return result
        acc, svc, fld = self._name_maps()
        pairs: dict[int, list[int]] = {i: [] for i in server_ids}
        for chunk in self._chunks(server_ids):
            ph = ",".join("?" * len(chunk))
            self.cursor.execute(
                f"SELECT server_id, account_id FROM server_links "
                f"WHERE server_id IN ({ph})", chunk)
            for r in self.cursor.fetchall():
                if r["server_id"] in pairs:
                    pairs[r["server_id"]].append(r["account_id"])
        for i, aids in pairs.items():
            rows = []
            for aid in aids:
                a = acc.get(aid)
                if not a or a[2]:            # нет записи или аккаунт в корзине
                    continue
                rows.append({"id": aid,
                             "name": self._path_from_maps(aid, acc, svc, fld)})
            rows.sort(key=lambda r: str(r["name"]).lower())
            result[i] = rows
        return result

    def _load_fin_links_bulk(self, item_ids):
        """{item_id: [{"id","name"(путь аккаунта)}]} — связанные живые аккаунты
        (в корзине не показываются), отсортированные по пути. Имена — через
        _name_maps (без запроса на каждую запись)."""
        result: dict[int, list[dict[str, Any]]] = {i: [] for i in item_ids}
        if not item_ids:
            return result
        acc, svc, fld = self._name_maps()
        pairs: dict[int, list[int]] = {i: [] for i in item_ids}
        for chunk in self._chunks(item_ids):
            ph = ",".join("?" * len(chunk))
            self.cursor.execute(
                f"SELECT item_id, account_id FROM fin_links "
                f"WHERE item_id IN ({ph})", chunk)
            for r in self.cursor.fetchall():
                if r["item_id"] in pairs:
                    pairs[r["item_id"]].append(r["account_id"])
        for i, aids in pairs.items():
            rows = []
            for aid in aids:
                a = acc.get(aid)
                if not a or a[2]:            # нет записи или аккаунт в корзине
                    continue
                rows.append({"id": aid,
                             "name": self._path_from_maps(aid, acc, svc, fld)})
            rows.sort(key=lambda r: str(r["name"]).lower())
            result[i] = rows
        return result

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
