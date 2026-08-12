"""Скрытие пустых полей/вкладок карточек и общая кнопка дерева."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QTreeWidget, QTreeWidgetItem

from hranilka.core.fin_types import FIN_TYPES
from hranilka.ui.fin_tabs import FinItemTabs
from hranilka.ui.server_tabs import ServerTabs
from hranilka.ui.tabs import AccountTabs
from hranilka.ui.tree import TreeMixin


def _visible_titles(tabs):
    return [button.text() for index, button in enumerate(tabs.tab_buttons())
            if tabs.isTabVisible(index)]


def test_account_read_mode_hides_empty_fields_and_tabs(qapp, pure_config):
    tabs = AccountTabs(config=pure_config)
    tabs.f_name.set_text("Аккаунт")

    # Имитируем сохранение с открытой пустой вкладки: она скрывается, а стек
    # безопасно возвращается на первую содержательную вкладку.
    tabs.set_all_editable(True)
    tabs.setCurrentIndex(4)  # «Фраза восстановления»
    tabs.set_all_editable(False)

    assert _visible_titles(tabs) == ["База"]
    assert tabs.currentIndex() == 0
    assert not tabs.f_name.isHidden()
    assert tabs.f_url.isHidden()
    assert tabs.f_device_id.isHidden()

    tabs.set_all_editable(True)
    assert len(_visible_titles(tabs)) == tabs.count()
    assert not tabs.f_device_id.isHidden()


def test_hide_empty_setting_can_be_disabled(qapp, pure_config):
    pure_config.set("hide_empty_card_fields", False)
    tabs = AccountTabs(config=pure_config)
    tabs.f_name.set_text("Аккаунт")
    tabs.set_all_editable(False)

    assert len(_visible_titles(tabs)) == tabs.count()
    assert not tabs.f_url.isHidden()
    assert not tabs.f_device_id.isHidden()


def test_appearance_page_exposes_hide_empty_checkbox(qapp, pure_config):
    from hranilka.ui.dialogs import SettingsDialog

    dialog = SettingsDialog(pure_config)
    assert dialog._tabs.tab_buttons()[0].text() == "Внешний вид"
    assert dialog.hide_empty_card_fields_check.isChecked()
    assert dialog._collect_settings()["hide_empty_card_fields"] is True
    dialog.deleteLater()


def test_fin_read_mode_keeps_only_tabs_with_content(qapp, pure_config):
    tabs = FinItemTabs(FIN_TYPES["bank_card"], config=pure_config)
    tabs.f_name.set_text("Карта")
    tabs.load_payload({"cvv": "123"})
    tabs.set_all_editable(False)

    assert _visible_titles(tabs) == ["База", "Реквизиты"]
    assert not tabs._widgets["cvv"].isHidden()
    assert tabs._widgets["card_number"].isHidden()
    assert tabs._widgets["currency"].isHidden()


def test_server_read_mode_keeps_filled_tab_and_edit_restores_all(
        qapp, pure_config):
    tabs = ServerTabs(config=pure_config)
    tabs.f_name.set_text("VPS")
    tabs.load_payload({"notes": "Важная заметка"})
    tabs.set_all_editable(False)

    assert _visible_titles(tabs) == ["База", "Заметки"]
    assert tabs._widgets["hosting"].isHidden()
    assert not tabs.f_notes.isHidden()

    tabs.set_all_editable(True)
    assert len(_visible_titles(tabs)) == tabs.count()
    assert not tabs._widgets["hosting"].isHidden()


def test_required_name_keeps_base_visible_for_every_card(qapp, pure_config):
    """Даже пустое обязательное название и его «База» не скрываются."""
    cards = (
        AccountTabs(config=pure_config),
        FinItemTabs(FIN_TYPES["bank_card"], config=pure_config),
        FinItemTabs(FIN_TYPES["crypto_wallet"], config=pure_config),
        ServerTabs(config=pure_config),
    )

    for tabs in cards:
        tabs.f_name.set_text("   ")
        tabs.set_all_editable(False)
        assert tabs.tab_buttons()[0].text() == "База"
        assert tabs.isTabVisible(0)
        assert not tabs.f_name.isHidden()


class _TreeHost(TreeMixin):
    def __init__(self, config):
        self.config = config
        self.tree = QTreeWidget()
        self.tree_toggle_btn = QPushButton()
        self._dirty_ids = set()
        self.search_text = ""


def _painted_bounds(icon):
    image = icon.pixmap(24, 24).toImage()
    points = [(x, y) for y in range(image.height())
              for x in range(image.width())
              if image.pixelColor(x, y).alpha()]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return max(xs) - min(xs), max(ys) - min(ys)


def test_tree_button_toggles_all_and_uses_distinct_tree_sizes(
        qapp, pure_config):
    host = _TreeHost(pure_config)
    root = QTreeWidgetItem(host.tree, ["root"])
    branch = QTreeWidgetItem(root, ["branch"])
    QTreeWidgetItem(branch, ["leaf"])
    root.setExpanded(True)
    branch.setExpanded(True)

    host._update_tree_toggle_btn()
    assert host.tree_toggle_btn.toolTip() == "Свернуть всё дерево"
    assert not host.tree_toggle_btn.icon().isNull()
    host.on_tree_toggle_all()
    assert not root.isExpanded()
    assert not branch.isExpanded()
    assert host.tree_toggle_btn.toolTip() == "Развернуть всё дерево"

    host.on_tree_toggle_all()
    assert root.isExpanded()
    assert branch.isExpanded()
    assert _painted_bounds(host._tree_action_icon(True))[0] > \
        _painted_bounds(host._tree_action_icon(False))[0]


def test_tree_button_disabled_for_empty_or_flat_tree(qapp, pure_config):
    host = _TreeHost(pure_config)
    host._update_tree_toggle_btn()
    assert not host.tree_toggle_btn.isEnabled()
    assert host.tree_toggle_btn.toolTip() == "В дереве нет вложенных ветвей"
    assert not host.tree_toggle_btn.icon().isNull()

    QTreeWidgetItem(host.tree, ["leaf"])
    host._update_tree_toggle_btn()
    assert not host.tree_toggle_btn.isEnabled()


def test_tree_button_does_not_collapse_search_results(qapp, pure_config):
    host = _TreeHost(pure_config)
    root = QTreeWidgetItem(host.tree, ["root"])
    root.setData(0, Qt.UserRole, {"name": "root"})
    child = QTreeWidgetItem(root, ["match"])
    child.setData(0, Qt.UserRole, {"name": "match"})

    host.on_search_changed("match")
    assert root.isExpanded()
    assert not host.tree_toggle_btn.isEnabled()
    assert host.tree_toggle_btn.toolTip() == \
        "Сворачивание дерева недоступно во время поиска"

    host.on_tree_toggle_all()
    assert root.isExpanded()

    host.on_search_changed("")
    assert host.tree_toggle_btn.isEnabled()
