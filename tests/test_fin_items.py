"""CRUD финансовых записей (DbFinItemsMixin): payload round-trip, экстракт-
колонки, связи с аккаунтами, корзина, избранное, порядок, узлы дерева.
Плюс модель FinItemData.
"""
from datetime import date

import pytest

from hranilka.core.nodetypes import CARD
from hranilka.data.models.fin_item import FinItemData


# ----- Модель FinItemData -----

def test_fin_item_data_roundtrip():
    d = FinItemData("bank_card", "Tinkoff")
    d.payload = {"card_number": "4111111111111234", "expiry": "12/25"}
    storage = d.to_storage()
    assert storage["payload"]["v"] == 1
    back = FinItemData.from_storage(storage)
    assert back.item_type == "bank_card"
    assert back.name == "Tinkoff"
    assert back.payload["card_number"] == "4111111111111234"


def test_fin_item_data_tolerant_empty():
    d = FinItemData.from_storage(None)
    assert d.item_type == "bank_card"
    assert d.gallery == []
    # v проставляется при сериализации (to_storage), даже если payload был пуст
    assert d.to_storage()["payload"]["v"] == 1


# ----- add / load / save -----

def test_add_and_load_fin_item(db):
    sid = db.add_service("Банк")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    storage = db.load_fin_item(iid)
    assert storage is not None
    assert storage["item_type"] == "bank_card"
    assert storage["name"] == "Карта"
    assert storage["payload"] == {}
    assert storage["gallery"] == []


def test_load_missing_returns_none(db):
    assert db.load_fin_item(999) is None


def test_save_payload_roundtrip_and_extracts(db):
    iid = db.add_fin_item(None, "bank_card", "Карта")
    storage = db.load_fin_item(iid)
    storage["name"] = "Tinkoff Black"
    storage["payload"] = {"v": 1, "card_number": "4111 1111 1111 1234",
                          "expiry": "12/25", "cvv": "123"}
    db.save_fin_item(iid, storage)

    again = db.load_fin_item(iid)
    assert again["name"] == "Tinkoff Black"
    assert again["payload"]["card_number"] == "4111 1111 1111 1234"
    assert again["payload"]["cvv"] == "123"
    # Экстракт-колонки пересчитаны из payload
    db.cursor.execute(
        "SELECT card_last4, expires_on FROM fin_items WHERE id = ?", (iid,))
    row = db.cursor.fetchone()
    assert row["card_last4"] == "1234"
    assert row["expires_on"] == date(2025, 12, 31).isoformat()


def test_save_updates_extracts_on_change(db):
    iid = db.add_fin_item(None, "bank_card", "К")
    storage = db.load_fin_item(iid)
    storage["payload"] = {"card_number": "5500000000009999", "expiry": "01/28"}
    db.save_fin_item(iid, storage)
    storage2 = db.load_fin_item(iid)
    storage2["payload"] = {"card_number": "", "expiry": ""}
    db.save_fin_item(iid, storage2)
    db.cursor.execute(
        "SELECT card_last4, expires_on FROM fin_items WHERE id = ?", (iid,))
    row = db.cursor.fetchone()
    assert row["card_last4"] is None
    assert row["expires_on"] is None


def test_save_missing_raises(db):
    with pytest.raises(ValueError):
        db.save_fin_item(999, {"item_type": "bank_card", "name": "x",
                               "payload": {}, "gallery": []})


# ----- Связи элемент ↔ аккаунт -----

def test_item_links_both_sides(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    a2 = db.add_account(sid, "A2")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.set_item_links(iid, [a1, a2])
    assert set(db.get_item_links(iid)) == {a1, a2}
    fin_of_a1 = db.get_account_fin_links(a1)
    assert len(fin_of_a1) == 1
    assert fin_of_a1[0]["id"] == iid
    assert fin_of_a1[0]["item_type"] == "bank_card"


def test_item_links_dedup_and_replace(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    a2 = db.add_account(sid, "A2")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.set_item_links(iid, [a1, a1, a1])        # дубли схлопываются
    assert db.get_item_links(iid) == [a1]
    db.set_item_links(iid, [a2])                # перезапись
    assert db.get_item_links(iid) == [a2]


def test_account_fin_links_carries_last4(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    storage = db.load_fin_item(iid)
    storage["payload"] = {"card_number": "4111111111111234"}
    db.save_fin_item(iid, storage)
    db.set_item_links(iid, [a1])
    assert db.get_account_fin_links(a1)[0]["card_last4"] == "1234"


def test_save_with_links_atomic(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    storage = db.load_fin_item(iid)
    storage["name"] = "N"
    db.save_fin_item_with_links(iid, storage, [a1])
    assert db.get_item_links(iid) == [a1]
    assert db.load_fin_item(iid)["name"] == "N"


def test_link_cascade_on_account_delete(db):
    sid = db.add_service("S")
    a1 = db.add_account(sid, "A1")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.set_item_links(iid, [a1])
    db.delete_account(a1)                        # FK CASCADE убирает связь
    assert db.get_item_links(iid) == []


# ----- Корзина -----

def test_fin_bin_move_restore(db):
    sid = db.add_service("S")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.move_fin_item_to_bin(iid)
    # В дереве записи из корзины нет
    assert not _find_fin(db.get_tree_structure(), iid)
    assert db.get_deleted_count() == 1
    deleted = db.get_deleted_fin_items()
    assert deleted[0]["id"] == iid
    assert deleted[0]["type"] == CARD
    db.restore_fin_item(iid)
    assert _find_fin(db.get_tree_structure(), iid)
    assert db.get_deleted_count() == 0


def test_empty_bin_clears_fin_items(db):
    sid = db.add_service("S")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    aid = db.add_account(sid, "A")
    db.move_fin_item_to_bin(iid)
    db.move_account_to_bin(aid)
    assert db.get_deleted_count() == 2
    db.empty_bin()
    assert db.get_deleted_count() == 0
    db.cursor.execute("SELECT COUNT(*) AS n FROM fin_items")
    assert db.cursor.fetchone()["n"] == 0


def test_get_deleted_records_combines(db):
    sid = db.add_service("S")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    aid = db.add_account(sid, "A")
    db.move_account_to_bin(aid)
    db.move_fin_item_to_bin(iid)
    records = db.get_deleted_records()
    types = {r["type"] for r in records}
    assert types == {"account", CARD}
    assert len(records) == 2


def test_delete_items_accepts_fin_leaf(db):
    sid = db.add_service("S")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    affected = db.delete_items([("card", iid)], to_bin=True)
    assert affected == [(CARD, iid)]
    assert db.get_deleted_count() == 1
    # Безвозвратное удаление
    iid2 = db.add_fin_item(sid, "bank_card", "Карта2")
    db.delete_items([("card", iid2)], to_bin=False)
    assert db.load_fin_item(iid2) is None


def test_delete_service_keep_content_preserves_fin_items(db):
    """Сервис удаляется, но все его листья становятся свободными, включая fin."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "A")
    card = db.add_fin_item(sid, "bank_card", "Карта")
    wallet = db.add_fin_item(sid, "crypto_wallet", "Кошелёк")
    db.set_item_links(card, [aid])

    db.delete_service_keep_content(sid)

    assert db.load_fin_item(card) is not None
    assert db.load_fin_item(wallet) is not None
    assert db.get_item_links(card) == [aid]
    db.cursor.execute("SELECT service_id FROM accounts WHERE id = ?", (aid,))
    assert db.cursor.fetchone()["service_id"] is None
    db.cursor.execute("SELECT service_id FROM fin_items WHERE id IN (?, ?) ORDER BY id",
                      (card, wallet))
    assert [r["service_id"] for r in db.cursor.fetchall()] == [None, None]


# ----- Избранное, порядок, перемещение -----

def test_fin_favorite(db):
    sid = db.add_service("S")
    iid = db.add_fin_item(sid, "bank_card", "Карта")
    db.set_fin_favorite(iid, True)
    node = _find_fin(db.get_tree_structure(), iid)
    assert node["is_favorite"] is True


def test_fin_items_order(db):
    sid = db.add_service("S")
    i1 = db.add_fin_item(sid, "bank_card", "B")
    i2 = db.add_fin_item(sid, "bank_card", "A")
    db.set_fin_items_order([i2, i1])
    db.cursor.execute(
        "SELECT id FROM fin_items ORDER BY sort_order")
    assert [r["id"] for r in db.cursor.fetchall()] == [i2, i1]


def test_move_fin_item(db):
    s1 = db.add_service("S1")
    s2 = db.add_service("S2")
    iid = db.add_fin_item(s1, "bank_card", "Карта")
    db.move_fin_item(iid, s2)
    db.cursor.execute("SELECT service_id FROM fin_items WHERE id = ?", (iid,))
    assert db.cursor.fetchone()["service_id"] == s2


# ----- Дерево -----

def test_tree_contains_fin_node(db):
    sid = db.add_service("Банк")
    iid = db.add_fin_item(sid, "bank_card", "Tinkoff")
    storage = db.load_fin_item(iid)
    storage["payload"] = {"card_number": "4111111111111234", "expiry": "12/40"}
    db.save_fin_item(iid, storage)
    tree = db.get_tree_structure()
    node = _find_fin(tree, iid)
    assert node is not None
    assert node["type"] == CARD
    assert node["name"] == "Tinkoff"
    assert node["card_last4"] == "1234"
    assert node["days_until_expiry"] > 0


def test_tree_free_fin_item_at_root(db):
    iid = db.add_fin_item(None, "bank_card", "Свободная")
    tree = db.get_tree_structure()
    assert any(n.get("id") == iid and n.get("type") == CARD for n in tree)


def test_unknown_item_type_skipped_in_tree(db):
    """Запись неизвестного (не в реестре) типа пропускается — дерево не падает."""
    sid = db.add_service("S")
    good = db.add_fin_item(sid, "bank_card", "OK")
    bad = db.add_fin_item(sid, "alien_type", "Чужой")   # тип вне реестра
    tree = db.get_tree_structure()                       # не должно упасть
    assert _find_fin(tree, good) is not None
    # Узел чужого типа в дереве отсутствует (пропущен _build_fin_node → None).
    all_ids = []

    def collect(nodes):
        for n in nodes:
            all_ids.append(n.get("id"))
            collect(n.get("children", []))
    collect(tree)
    service = next(n for n in tree if n.get("type") == "service")
    assert bad not in [c.get("id") for c in service["children"]]


# ----- helpers -----

def _find_fin(tree, item_id):
    """Рекурсивно ищет fin-узел (card/wallet) по id в структуре дерева."""
    for n in tree:
        if n.get("type") in ("card", "wallet") and n.get("id") == item_id:
            return n
        found = _find_fin(n.get("children", []), item_id)
        if found:
            return found
    return None
