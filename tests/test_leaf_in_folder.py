"""Лист (аккаунт/карта/кошелёк/сервер) прямо в папке, минуя сервис (схема v13).

Миграция v12→v13, CRUD, инвариант «максимум один родитель», перенос
сервис↔папка↔корень, удаление папки (каскад / keep_content / корзина),
пути, экспорт, DnD/меню на уровне логики.
"""
import re
import sqlite3

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QAbstractItemView, QTreeWidgetItem

from hranilka.core.nodetypes import ACCOUNT, CARD, FOLDER, SERVER, SERVICE
from hranilka.data.database import Database, SCHEMA_VERSION
from hranilka.ui.tree import AccountTree, TreeMixin


# ----- helpers -----

def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.get("children", []))


def _folder(tree, folder_id):
    return next(n for n in _walk(tree)
                if n["type"] == FOLDER and n["id"] == folder_id)


def _kinds(node):
    return [(c["type"], c["id"]) for c in node["children"]]


def _count(db, sql, params=()):
    db.cursor.execute(sql, params)
    return db.cursor.fetchone()[0]


def _leaf_cols(db, table):
    return {r["name"] for r in db.cursor.execute(f"PRAGMA table_info({table})")}


def _make_v12_file(path):
    """Файл БД v12: схема текущего кода без folder_id у листьев + данные."""
    d = Database(path)
    d.connect()
    d.create_tables()
    fid = d.add_folder("Папка")
    sid = d.add_service("Сервис", fid)
    d.add_account(sid, "Акк-в-сервисе")
    d.add_account(None, "Акк-в-корне")
    d.add_fin_item(None, "bank_card", "Карта")
    d.add_server(sid, "VPS")
    d._commit()
    con = d.conn
    con.execute("PRAGMA foreign_keys = OFF")
    for table in ("accounts", "fin_items", "servers"):
        sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?", (table,)
        ).fetchone()[0]
        stripped = re.sub(r"\n\s*folder_id INTEGER REFERENCES[^\n]*,", "", sql)
        assert stripped != sql
        con.execute(f"DROP INDEX IF EXISTS idx_{table}_folder")
        tmp = table + "_v12"
        con.execute(stripped.replace(f"CREATE TABLE {table}",
                                     f"CREATE TABLE {tmp}", 1))
        cols = ", ".join(r[1] for r in con.execute(f"PRAGMA table_info({tmp})"))
        con.execute(f"INSERT INTO {tmp} ({cols}) SELECT {cols} FROM {table}")
        con.execute(f"DROP TABLE {table}")
        con.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
    d.set_schema_version(12)
    d._commit()
    con.execute("PRAGMA foreign_keys = ON")
    d.close(persist=False)
    d.shutdown_executor()


# ----- миграция -----

def test_migration_v12_to_v13_adds_columns_and_keeps_data(tmp_path):
    path = str(tmp_path / "v12.db")
    _make_v12_file(path)
    raw = sqlite3.connect(path)
    for table in ("accounts", "fin_items", "servers"):
        cols = {r[1] for r in raw.execute(f"PRAGMA table_info({table})")}
        assert "folder_id" not in cols
    raw.close()

    d = Database(path)
    d.connect()
    try:
        d.create_tables()
        assert d.get_schema_version() == SCHEMA_VERSION == 13
        for table in ("accounts", "fin_items", "servers"):
            assert "folder_id" in _leaf_cols(d, table)
            idx = {r["name"] for r in d.cursor.execute(
                f"PRAGMA index_list({table})")}
            assert f"idx_{table}_folder" in idx
        assert _count(d, "SELECT COUNT(*) FROM accounts") == 2
        assert _count(d, "SELECT COUNT(*) FROM accounts "
                         "WHERE folder_id IS NOT NULL") == 0
        # иерархия не изменилась
        tree = d.get_tree_structure(include_servers=True)
        folder = next(n for n in tree if n["type"] == FOLDER)
        assert [c["type"] for c in folder["children"]] == [SERVICE]
        # после миграции лист в папке создаётся
        acc = d.add_account(None, "Новый", folder_id=folder["id"])
        assert acc
    finally:
        d.close(persist=False)
        d.shutdown_executor()


def test_migration_is_idempotent(tmp_path):
    from hranilka.data.database.migrations.m013_leaf_folder import migrate
    path = str(tmp_path / "v12.db")
    _make_v12_file(path)
    con = sqlite3.connect(path)
    migrate(con)
    migrate(con)                         # повтор не падает и не дублирует
    for table in ("accounts", "fin_items", "servers"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        assert cols.count("folder_id") == 1
    con.close()


def test_check_constraint_blocks_two_parents(db):
    fid = db.add_folder("П")
    sid = db.add_service("С")
    aid = db.add_account(None, "А")
    with pytest.raises(sqlite3.IntegrityError):
        db.cursor.execute(
            "UPDATE accounts SET service_id = ?, folder_id = ? WHERE id = ?",
            (sid, fid, aid))
    db.conn.rollback()


# ----- CRUD -----

def test_add_leaves_in_folder_and_tree_placement(db):
    fid = db.add_folder("Папка")
    sid = db.add_service("Сервис", fid)
    svc_acc = db.add_account(sid, "в сервисе")
    acc = db.add_account(None, "в папке", folder_id=fid)
    card = db.add_fin_item(None, "bank_card", "Карта", folder_id=fid)
    srv = db.add_server(None, "VPS", folder_id=fid)
    root_acc = db.add_account(None, "корень")

    tree = db.get_tree_structure(include_servers=True)
    folder = _folder(tree, fid)
    # сначала сервисы, затем листья папки
    assert _kinds(folder) == [(SERVICE, sid), (ACCOUNT, acc), (CARD, card),
                              (SERVER, srv)]
    service = folder["children"][0]
    assert _kinds(service) == [(ACCOUNT, svc_acc)]
    # свободный лист — только корень, листья папки в корень не просачиваются
    root_leaves = [(n["type"], n["id"]) for n in tree if n["type"] != FOLDER]
    assert root_leaves == [(ACCOUNT, root_acc)]


def test_add_with_both_parents_rejected(db):
    fid = db.add_folder("П")
    sid = db.add_service("С")
    with pytest.raises(ValueError):
        db.add_account(sid, "x", folder_id=fid)
    with pytest.raises(ValueError):
        db.add_fin_item(sid, "bank_card", "x", folder_id=fid)
    with pytest.raises(ValueError):
        db.add_server(sid, "x", folder_id=fid)
    assert _count(db, "SELECT COUNT(*) FROM accounts") == 0


def test_add_account_with_card_in_folder(db):
    from hranilka.data.models import AccountData
    fid = db.add_folder("П")
    data = AccountData()
    data.name = "Карточный"
    aid = db.add_account_with_card(None, "Карточный", data.to_storage(), fid)
    db.cursor.execute("SELECT service_id, folder_id FROM accounts WHERE id=?",
                      (aid,))
    row = db.cursor.fetchone()
    assert (row["service_id"], row["folder_id"]) == (None, fid)


# ----- перенос -----

def test_move_between_service_folder_root(db):
    fid = db.add_folder("П")
    sid = db.add_service("С")
    aid = db.add_account(sid, "А")

    def parents():
        db.cursor.execute("SELECT service_id, folder_id FROM accounts WHERE id=?",
                          (aid,))
        r = db.cursor.fetchone()
        return r["service_id"], r["folder_id"]

    db.move_account(aid, None, folder_id=fid)
    assert parents() == (None, fid)
    db.move_account(aid, sid)                      # папка -> сервис
    assert parents() == (sid, None)
    db.move_accounts([aid], None, fid)
    assert parents() == (None, fid)
    db.move_account(aid, None)                     # папка -> корень
    assert parents() == (None, None)


def test_move_fin_and_server_to_folder_and_back(db):
    fid = db.add_folder("П")
    sid = db.add_service("С")
    card = db.add_fin_item(sid, "bank_card", "К")
    srv = db.add_server(sid, "S")
    db.move_fin_item(card, None, folder_id=fid)
    db.move_servers([srv], None, fid)
    folder = _folder(db.get_tree_structure(include_servers=True), fid)
    assert set(_kinds(folder)) == {(CARD, card), (SERVER, srv)}
    db.move_fin_items([card], sid)
    db.move_server(srv, None)
    folder = _folder(db.get_tree_structure(include_servers=True), fid)
    assert _kinds(folder) == []


def test_move_with_two_parents_rejected_and_unchanged(db):
    fid = db.add_folder("П")
    sid = db.add_service("С")
    aid = db.add_account(sid, "А")
    with pytest.raises(ValueError):
        db.move_account(aid, sid, folder_id=fid)
    assert db.get_account_path(aid) == "С / А"


def test_move_leaves_mixed_types_atomic(db):
    fid = db.add_folder("П")
    aid = db.add_account(None, "А")
    card = db.add_fin_item(None, "bank_card", "К")
    srv = db.add_server(None, "S")
    db.move_leaves([(ACCOUNT, aid), (CARD, card), (SERVER, srv)],
                   folder_id=fid)
    folder = _folder(db.get_tree_structure(include_servers=True), fid)
    assert set(_kinds(folder)) == {(ACCOUNT, aid), (CARD, card), (SERVER, srv)}
    # неизвестный тип откатывает всю операцию
    other = db.add_folder("Другая")
    with pytest.raises(ValueError):
        db.move_leaves([(ACCOUNT, aid), (FOLDER, 1)], folder_id=other)
    assert _kinds(_folder(db.get_tree_structure(include_servers=True),
                          other)) == []
    db.move_leaves([(ACCOUNT, aid), (CARD, card), (SERVER, srv)])   # в корень
    assert _kinds(_folder(db.get_tree_structure(include_servers=True),
                          fid)) == []


# ----- удаление папки -----

def _folder_with_everything(db):
    fid = db.add_folder("П")
    sid = db.add_service("С", fid)
    ids = {
        "svc_acc": db.add_account(sid, "в сервисе"),
        "acc": db.add_account(None, "в папке", folder_id=fid),
        "card": db.add_fin_item(None, "bank_card", "К", folder_id=fid),
        "srv": db.add_server(None, "S", folder_id=fid),
        "outside": db.add_account(None, "снаружи"),
    }
    return fid, sid, ids


def test_delete_folder_cascades_to_direct_leaves(db):
    fid, _sid, ids = _folder_with_everything(db)
    affected = db.delete_items([(FOLDER, fid)])
    assert {(ACCOUNT, ids["acc"]), (ACCOUNT, ids["svc_acc"]),
            (CARD, ids["card"]), (SERVER, ids["srv"])} == set(affected)
    assert _count(db, "SELECT COUNT(*) FROM accounts") == 1       # «снаружи»
    assert _count(db, "SELECT COUNT(*) FROM fin_items") == 0
    assert _count(db, "SELECT COUNT(*) FROM servers") == 0
    assert _count(db, "SELECT COUNT(*) FROM folders") == 0


def test_delete_folder_keep_content_moves_leaves_to_root(db):
    fid, sid, ids = _folder_with_everything(db)
    db.delete_items([(FOLDER, fid)], keep=True)
    tree = db.get_tree_structure(include_servers=True)
    assert not any(n["type"] == FOLDER for n in tree)
    root = {(n["type"], n["id"]) for n in tree}
    assert {(SERVICE, sid), (ACCOUNT, ids["acc"]), (CARD, ids["card"]),
            (SERVER, ids["srv"]), (ACCOUNT, ids["outside"])} <= root
    assert _count(db, "SELECT COUNT(*) FROM accounts "
                      "WHERE folder_id IS NOT NULL") == 0


def test_delete_folder_keep_content_method(db):
    fid, _sid, ids = _folder_with_everything(db)
    db.delete_folder_keep_content(fid)
    assert db.get_account_path(ids["acc"]) == "в папке"


def test_delete_folder_method_and_descendants(db):
    fid, _sid, ids = _folder_with_everything(db)
    assert set(db.get_descendant_account_ids("folder", fid)) == {
        ids["acc"], ids["svc_acc"]}
    keys = db._descendant_leaf_keys_rows("folder", fid)
    assert set(keys) == {(ACCOUNT, ids["acc"]), (ACCOUNT, ids["svc_acc"]),
                         (CARD, ids["card"]), (SERVER, ids["srv"])}
    db.delete_folder(fid)
    assert _count(db, "SELECT COUNT(*) FROM accounts") == 1


def test_leaf_in_folder_to_bin_and_restore(db):
    fid = db.add_folder("П")
    aid = db.add_account(None, "А", folder_id=fid)
    db.delete_items([(ACCOUNT, aid)], to_bin=True)
    assert _kinds(_folder(db.get_tree_structure(), fid)) == []
    db.restore_account(aid)
    assert _kinds(_folder(db.get_tree_structure(), fid)) == [(ACCOUNT, aid)]


# ----- пути и экспорт -----

def test_path_folder_leaf_and_full_path(db):
    fid = db.add_folder("Папка")
    sid = db.add_service("Сервис", fid)
    in_folder = db.add_account(None, "Лист", folder_id=fid)
    in_service = db.add_account(sid, "Лист2")
    root = db.add_account(None, "Корень")
    assert db.get_account_path(in_folder) == "Папка / Лист"
    assert db.get_account_path(in_service) == "Папка / Сервис / Лист2"
    assert db.get_account_path(root) == "Корень"
    names = {r["id"]: r["name"] for r in db.get_all_accounts()}
    assert names[in_folder] == "Папка / Лист"
    assert names[root] == "Корень"


def test_links_show_folder_path(db):
    fid = db.add_folder("Папка")
    a = db.add_account(None, "A", folder_id=fid)
    b = db.add_account(None, "B")
    db.set_links(a, [b])
    assert db.get_links(b) == [{"id": a, "name": "Папка / A"}]


def test_export_subtree_contains_folder_leaves(db):
    fid = db.add_folder("Папка")
    aid = db.add_account(None, "Лист", folder_id=fid)
    srv = db.add_server(None, "VPS", folder_id=fid)
    tree = db.export_subtree()
    folder = _folder(tree, fid)
    acc_node = next(c for c in folder["children"] if c["type"] == ACCOUNT)
    assert acc_node["id"] == aid and acc_node["card"] is not None
    srv_node = next(c for c in folder["children"] if c["type"] == SERVER)
    assert srv_node["id"] == srv and srv_node["server"] is not None
    sub = db.export_subtree(FOLDER, fid)
    assert len(sub) == 1 and len(sub[0]["children"]) == 2
    selected = db.export_selected([(ACCOUNT, aid)])
    assert [n["id"] for n in selected] == [aid]


def test_all_export_formats_include_folder_leaf(db, tmp_path):
    from hranilka.services import export
    from hranilka.services.export import Options
    fid = db.add_folder("Папка")
    db.add_account(None, "ЛистВПапке", login="u", password="p", folder_id=fid)
    theme = {"font": "Consolas", "font_size": 14, "text_color": "#000000",
             "main_bg_color": "#F0F0F0", "tree_bg_color": "#FFFFFF"}
    tree = db.export_subtree()
    html = export._html_document(tree, Options(theme=theme, title="Т"))
    assert "Папка" in html and "ЛистВПапке" in html
    for _fmt, (fn, ext, _flt) in export.FORMATS.items():
        out = tmp_path / ("exp" + ext)
        fn(tree, Options(theme=theme, title="Т"), str(out))
        assert out.stat().st_size > 0
    txt = (tmp_path / "exp.txt").read_text(encoding="utf-8")
    assert "Папка" in txt and "ЛистВПапке" in txt


# ----- DnD / меню (логика) -----

def _item(parent, item_id, name, item_type=ACCOUNT):
    item = QTreeWidgetItem(parent, [name])
    item.setData(0, Qt.ItemDataRole.UserRole,
                 {"id": item_id, "name": name, "type": item_type,
                  "is_favorite": False})
    return item


def _tree(qapp):
    tree = AccountTree()
    tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    tree.resize(420, 300)
    tree.show()
    qapp.processEvents()
    return tree


def _center(tree, item):
    rect = tree.visualItemRect(item)
    return QPoint(rect.center().x(), rect.center().y())


def test_reparent_plan_allows_leaf_drop_on_folder(qapp):
    tree = _tree(qapp)
    folder = _item(tree, 1, "Папка", FOLDER)
    leaf = _item(tree, 2, "Лист")
    card = _item(tree, 3, "Карта", CARD)
    folder.setExpanded(True)
    leaf.setSelected(True)
    card.setSelected(True)
    qapp.processEvents()
    plan, hint = tree._reparent_plan(_center(tree, folder))
    assert plan is not None
    sources, target = plan
    assert target is folder and set(sources) == {leaf, card}
    assert "Папка" in hint


def test_reparent_plan_rejects_services_and_same_parent(qapp):
    tree = _tree(qapp)
    folder = _item(tree, 1, "Папка", FOLDER)
    inside = _item(folder, 2, "Внутри")
    service = _item(tree, 3, "Сервис", SERVICE)
    folder.setExpanded(True)
    qapp.processEvents()
    inside.setSelected(True)
    plan, _ = tree._reparent_plan(_center(tree, folder))
    assert plan is None                          # уже в этой папке
    inside.setSelected(False)
    service.setSelected(True)
    plan, _ = tree._reparent_plan(_center(tree, folder))
    assert plan is None                          # сервис — не лист
    leaf = _item(tree, 9, "Лист")
    qapp.processEvents()
    service.setSelected(False)
    leaf.setSelected(True)
    plan, _ = tree._reparent_plan(_center(tree, service))
    assert plan is None                          # цель не папка


def test_drop_event_emits_reparent(qapp):
    from types import SimpleNamespace
    tree = _tree(qapp)
    folder = _item(tree, 1, "Папка", FOLDER)
    leaf = _item(tree, 2, "Лист")
    qapp.processEvents()
    leaf.setSelected(True)
    got = []
    tree.reparent_requested.connect(lambda items, tgt: got.append((items, tgt)))
    from PySide6.QtCore import QPointF
    center = _center(tree, folder)
    ev = SimpleNamespace(position=lambda: QPointF(center), accepted=None,
                         setDropAction=lambda a: None,
                         accept=lambda: None, ignore=lambda: None)
    tree.dropEvent(ev)
    assert got == [([leaf], folder)]


class _FakeTreeHost(TreeMixin):
    """Минимальный хост TreeMixin: без окна, только логика выбора родителя."""

    def __init__(self, tree):
        self.tree = tree


def test_new_leaf_parent_resolution(qapp):
    tree = _tree(qapp)
    folder = _item(tree, 10, "Папка", FOLDER)
    in_folder = _item(folder, 11, "Лист в папке")
    service = _item(folder, 12, "Сервис", SERVICE)
    in_service = _item(service, 13, "Лист в сервисе")
    root_leaf = _item(tree, 14, "Корень")
    host = _FakeTreeHost(tree)
    cases = [(folder, (None, 10)), (in_folder, (None, 10)),
             (service, (12, None)), (in_service, (12, None)),
             (root_leaf, (None, None))]
    for item, expected in cases:
        tree.setCurrentItem(item)
        assert host._new_leaf_parent() == expected
    tree.setCurrentItem(None)
    assert host._new_leaf_parent() == (None, None)


def test_order_groups_allowed_inside_folder(qapp):
    """В папке переупорядочиваются и сервисы, и листья (группы раздельно)."""
    tree = _tree(qapp)
    folder = _item(tree, 1, "Папка", FOLDER)
    a1 = _item(folder, 2, "a1")
    a2 = _item(folder, 3, "a2")
    folder.setExpanded(True)
    qapp.processEvents()
    a1.setSelected(True)
    from PySide6.QtCore import QPoint
    rect = tree.visualItemRect(a2)
    plan, _ = tree._drop_plan(QPoint(rect.center().x(), rect.bottom() - 2))
    assert plan is not None and plan[1] is folder


def test_select_node_expands_collapsed_ancestors(qapp):
    tree = _tree(qapp)
    folder = _item(tree, 1, "Папка", FOLDER)
    leaf = _item(folder, 2, "Лист")
    folder.setExpanded(False)

    class Host(TreeMixin):
        pass

    host = Host()
    host.tree = tree
    host._select_node(ACCOUNT, 2)
    assert folder.isExpanded() and tree.currentItem() is leaf
