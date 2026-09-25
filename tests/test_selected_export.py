"""Выборочный экспорт нескольких узлов дерева."""

import asyncio

from hranilka.core.nodetypes import ACCOUNT, FOLDER, SERVICE
from hranilka.services.export import Options


def test_export_selected_keeps_independent_branches_and_deduplicates_child(db):
    folder = db.add_folder("Папка")
    first_service = db.add_service("Первый", folder)
    second_service = db.add_service("Второй", folder)
    first_account = db.add_account(first_service, "Первый аккаунт")
    second_account = db.add_account(second_service, "Второй аккаунт")

    tree = db.export_selected([
        (FOLDER, folder), (SERVICE, first_service), (ACCOUNT, second_account),
    ])

    # Папка уже включает оба сервиса и аккаунты; отдельно выделенные потомки
    # не дублируют содержимое в итоговом документе.
    assert len(tree) == 1
    assert tree[0]["id"] == folder
    assert {child["id"] for child in tree[0]["children"]} == {
        first_service, second_service}
    accounts = {
        child["id"]: child["children"][0]["id"]
        for child in tree[0]["children"]
    }
    assert accounts == {first_service: first_account, second_service: second_account}


def test_export_selected_uses_tree_order_for_separate_nodes(db):
    first = db.add_service("Первый")
    second = db.add_service("Второй")

    tree = db.export_selected([(SERVICE, second), (SERVICE, first)])

    expected = [node["id"] for node in db.get_tree_structure()
                if node["id"] in {first, second}]
    assert [node["id"] for node in tree] == expected


def test_export_dialog_passes_selected_nodes_as_one_argument(qapp, pure_config,
                                                             monkeypatch):
    """Диалог не должен распаковывать список пар type/id в аргументы БД."""
    from hranilka.ui.dialogs import export_dialog as dialog_module

    captured = {}

    class Db:
        def export_selected(self, nodes, **kwargs):
            return []

        def current_session(self):
            return object()

        async def run_async(self, method, *args, **kwargs):
            captured["method"] = method
            captured["args"] = args
            return []

    monkeypatch.setattr(dialog_module, "themed_info", lambda *args: None)
    selected = ((SERVICE, 10), (ACCOUNT, 11))
    dialog = dialog_module.ExportDialog(
        pure_config, Db(), None, None, "Выбранные", selected_nodes=selected)
    asyncio.run(dialog._run_export(None, "unused", Options()))

    assert captured["args"] == (selected,)
    dialog.deleteLater()
