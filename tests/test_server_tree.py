"""Узел SERVER в дереве (tree_ops): include_servers, сортировка, избранное,
delete_items (корзина/навсегда), каскады удаления контейнеров, keep_content,
корзина (get_deleted_records/empty_bin). Сервер — независимая от fin_items
ветка (docs/ТЗ_VPS_Серверы.md §2), тесты не переиспользуют fin-хелперы.
"""
from hranilka.core.nodetypes import SERVER


def _find_server(nodes, server_id):
    """Рекурсивный поиск узла сервера по id в структуре дерева."""
    for n in nodes:
        if n.get("type") == SERVER and n.get("id") == server_id:
            return n
        found = _find_server(n.get("children", []), server_id)
        if found:
            return found
    return None


def _count(db, table, where="1", params=()):
    db.cursor.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params)
    return db.cursor.fetchone()["n"]


# ----- include_servers -----

def test_tree_excludes_servers_by_default(db):
    sid = db.add_service("Провайдер")
    srv = db.add_server(sid, "VPS-1")
    tree = db.get_tree_structure()               # include_servers=False (дефолт)
    assert _find_server(tree, srv) is None


def test_tree_includes_servers_when_requested(db):
    sid = db.add_service("Провайдер")
    srv = db.add_server(sid, "VPS-1")
    tree = db.get_tree_structure(include_servers=True)
    node = _find_server(tree, srv)
    assert node is not None
    assert node["name"] == "VPS-1"
    assert node["type"] == SERVER


def test_tree_free_server_at_root(db):
    srv = db.add_server(None, "Свободный")
    tree = db.get_tree_structure(include_servers=True)
    assert any(n.get("id") == srv and n.get("type") == SERVER for n in tree)


def test_deleted_server_not_in_tree(db):
    sid = db.add_service("S")
    srv = db.add_server(sid, "VPS")
    db.move_server_to_bin(srv)
    tree = db.get_tree_structure(include_servers=True)
    assert _find_server(tree, srv) is None


def test_server_paid_days_left_in_node(db):
    sid = db.add_service("S")
    srv = db.add_server(sid, "VPS")
    storage = db.get_server(srv)
    storage["payload"] = {"paid_until": "2000-01-01"}   # заведомо просрочено
    db.save_server(srv, storage)
    node = _find_server(db.get_tree_structure(include_servers=True), srv)
    assert node["paid_days_left"] is not None
    assert node["paid_days_left"] < 0


# ----- Сортировка / избранное -----

def test_servers_sorted_by_name(db):
    sid = db.add_service("S")
    db.add_server(sid, "Zeta")
    db.add_server(sid, "Alpha")
    tree = db.get_tree_structure(sort_mode="name", include_servers=True)
    service_node = next(n for n in tree if n["type"] == "service")
    names = [c["name"] for c in service_node["children"] if c["type"] == SERVER]
    assert names == ["Alpha", "Zeta"]


def test_favorite_server_on_top(db):
    sid = db.add_service("S")
    a = db.add_server(sid, "A")
    b = db.add_server(sid, "B")
    db.set_server_favorite(b, True)
    tree = db.get_tree_structure(include_servers=True)
    service_node = next(n for n in tree if n["type"] == "service")
    server_nodes = [c for c in service_node["children"] if c["type"] == SERVER]
    assert server_nodes[0]["id"] == b


def test_servers_are_siblings_after_accounts_and_fin(db):
    """leaf_nodes: аккаунты, затем фин-записи, затем серверы (§4 ТЗ)."""
    sid = db.add_service("S")
    db.add_account(sid, "Acc")
    db.add_fin_item(sid, "bank_card", "Карта")
    db.add_server(sid, "VPS")
    tree = db.get_tree_structure(include_fin=True, include_servers=True)
    service_node = next(n for n in tree if n["type"] == "service")
    types = [c["type"] for c in service_node["children"]]
    assert types == ["account", "card", SERVER]


# ----- delete_items -----

def test_delete_items_server_to_bin(db):
    sid = db.add_service("S")
    srv = db.add_server(sid, "VPS")
    affected = db.delete_items([(SERVER, srv)], to_bin=True)
    assert affected == [(SERVER, srv)]
    assert _count(db, "servers") == 1
    assert _count(db, "servers", "deleted_at IS NOT NULL") == 1


def test_delete_items_server_forever(db):
    sid = db.add_service("S")
    srv = db.add_server(sid, "VPS")
    affected = db.delete_items([(SERVER, srv)], to_bin=False)
    assert affected == [(SERVER, srv)]
    assert _count(db, "servers") == 0


def test_delete_items_unknown_type_still_raises(db):
    """Ветка ValueError на неизвестный тип узла осталась (не перехвачена
    новой веткой SERVER)."""
    import pytest
    with pytest.raises(ValueError):
        db.delete_items([("garbage", 1)])


def test_delete_items_service_cascades_server(db):
    """Каскадно удаляемые серверы возвращаются для очистки UI-черновиков,
    вместе с аккаунтами и фин-записями (по образцу fin-веток)."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    card = db.add_fin_item(sid, "bank_card", "Карта")
    srv = db.add_server(sid, "VPS")

    affected = db.delete_items([("service", sid)])

    assert set(affected) == {("account", aid), ("card", card), (SERVER, srv)}
    assert _count(db, "servers") == 0


def test_delete_items_folder_cascades_server(db):
    fid = db.add_folder("F")
    sid = db.add_service("S", fid)
    srv = db.add_server(sid, "VPS")

    affected = db.delete_items([("folder", fid)])

    assert affected == [(SERVER, srv)]
    assert _count(db, "servers") == 0


def test_delete_service_keep_content_frees_server(db):
    """keep=True: сервис удалён, сервер становится свободным (не удалён)."""
    sid = db.add_service("S")
    srv = db.add_server(sid, "VPS")

    affected = db.delete_items([("service", sid)], keep=True)

    assert affected == []
    assert _count(db, "servers", "id = ?", (srv,)) == 1
    db.cursor.execute("SELECT service_id FROM servers WHERE id = ?", (srv,))
    assert db.cursor.fetchone()["service_id"] is None


def test_delete_service_keep_content_method_frees_server(db):
    """Публичный delete_service_keep_content (не через delete_items) тоже
    освобождает сервер."""
    sid = db.add_service("S")
    srv = db.add_server(sid, "VPS")
    db.delete_service_keep_content(sid)
    db.cursor.execute("SELECT service_id FROM servers WHERE id = ?", (srv,))
    assert db.cursor.fetchone()["service_id"] is None


# ----- Корзина (общие методы) -----

def test_get_deleted_records_includes_server(db):
    sid = db.add_service("S")
    srv = db.add_server(sid, "VPS")
    aid = db.add_account(sid, "A")
    db.move_server_to_bin(srv)
    db.move_account_to_bin(aid)
    records = db.get_deleted_records()
    types = {r["type"] for r in records}
    assert types == {"account", SERVER}
    assert len(records) == 2
    srv_record = next(r for r in records if r["type"] == SERVER)
    assert srv_record["id"] == srv
    assert srv_record["name"] == "VPS"


def test_get_deleted_count_includes_server(db):
    sid = db.add_service("S")
    srv = db.add_server(sid, "VPS")
    db.move_server_to_bin(srv)
    assert db.get_deleted_count() == 1


def test_empty_bin_clears_servers(db):
    sid = db.add_service("S")
    srv = db.add_server(sid, "VPS")
    db.move_server_to_bin(srv)
    assert db.get_deleted_count() == 1
    db.empty_bin()
    assert db.get_deleted_count() == 0
    assert _count(db, "servers") == 0


# ----- Порядок (DnD) -----

def test_servers_order_reflected_in_tree(db):
    sid = db.add_service("S")
    s1 = db.add_server(sid, "B")
    s2 = db.add_server(sid, "A")
    db.set_servers_order([s2, s1])
    tree = db.get_tree_structure(sort_mode="manual", include_servers=True)
    service_node = next(n for n in tree if n["type"] == "service")
    ids = [c["id"] for c in service_node["children"] if c["type"] == SERVER]
    assert ids == [s2, s1]


# ----- Переименование -----

def test_rename_server(db):
    sid = db.add_service("S")
    srv = db.add_server(sid, "Старое имя")
    db.rename_server(srv, "Новое имя")
    assert db.get_server(srv)["name"] == "Новое имя"
