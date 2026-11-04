"""Панели статус-бара: состояние, счётчик записей, CAPS, часы, стиль."""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QMainWindow, QProgressBar,
                               QTreeWidget, QTreeWidgetItem)

from hranilka.core import nodetypes
from hranilka.ui import status_panels as sp
from hranilka.ui import theme


def _leaf(parent, type_, name="x"):
    it = QTreeWidgetItem(parent, [name])
    it.setData(0, Qt.ItemDataRole.UserRole, {"type": type_, "id": 1, "name": name})
    return it


def _make(qapp, encrypted=False, caps=False):
    win = QMainWindow()
    tree = QTreeWidget()
    state = {"enc": encrypted, "caps": caps}
    panels = sp.StatusPanels(win.statusBar(), tree, lambda: state["enc"], win,
                             caps_reader=lambda: state["caps"])
    return win, tree, panels, state


def test_pure_texts():
    assert sp.vault_state_text(True, False) == "[#] ШИФР."
    assert sp.vault_state_text(False, False) == "[ ] ОТКРЫТО"
    assert sp.vault_state_text(True, True) == "[X] ЗАБЛОКИРОВАНО"
    assert sp.count_text(5) == "Записей: 5"
    assert sp.clock_text(datetime(2026, 1, 2, 3, 4)) == "03:04"


def test_state_and_lock(qapp):
    win, tree, panels, state = _make(qapp, encrypted=True)
    assert panels.state_label.text() == "[#] ШИФР."
    panels.set_locked(True)
    assert "ЗАБЛОКИРОВАНО" in panels.state_label.text()
    panels.set_locked(False)
    state["enc"] = False
    panels._tick()
    assert panels.state_label.text() == "[ ] ОТКРЫТО"
    panels.stop()


def test_count_follows_tree_changes(qapp):
    win, tree, panels, _ = _make(qapp)
    assert panels.count_label.text() == "Записей: 0"
    folder = QTreeWidgetItem(tree, ["f"])
    folder.setData(0, Qt.ItemDataRole.UserRole, {"type": nodetypes.FOLDER, "id": 1})
    _leaf(folder, nodetypes.ACCOUNT)
    _leaf(folder, nodetypes.SERVER)
    QApplication.processEvents()
    assert panels.count_label.text() == "Записей: 2"
    tree.clear()
    QApplication.processEvents()
    assert panels.count_label.text() == "Записей: 0"
    panels.stop()


def test_caps_and_clock(qapp):
    win, tree, panels, state = _make(qapp, caps=True)
    assert panels.caps_label.isEnabled()
    state["caps"] = False
    panels._tick()
    assert not panels.caps_label.isEnabled()
    assert len(panels.clock_label.text()) == 5
    panels.stop()


def test_panels_are_permanent_and_styled(qapp, pure_config):
    win, tree, panels, _ = _make(qapp)
    win.setStyleSheet(theme.widget_styles(pure_config))
    win.resize(500, 200)
    win.show()
    QApplication.processEvents()
    bar = win.statusBar()
    for lbl in (panels.state_label, panels.count_label, panels.clock_label):
        assert lbl.property("statusPanel") == "true"
        assert bar.isAncestorOf(lbl) and lbl.isVisible()
    bar.showMessage("сообщение")                 # не ломает постоянные панели
    assert bar.currentMessage() == "сообщение"
    assert bar.isSizeGripEnabled()
    panels.stop()
    win.close()


def test_block_progress_bar_chunks(qapp, pure_config):
    """Chunk рисуется блоками: на полосе чередуются цвет блока и просвет."""
    bar = QProgressBar()
    bar.setStyleSheet(theme.widget_styles(pure_config))
    bar.setRange(0, 100)
    bar.setValue(100)
    bar.setTextVisible(False)
    bar.resize(240, 24)
    bar.show()
    QApplication.processEvents()
    img = bar.grab().toImage()
    y = img.height() // 2
    row = [img.pixelColor(x, y).name() for x in range(6, img.width() - 6)]
    assert len(set(row)) >= 2
    transitions = sum(1 for a, b in zip(row, row[1:]) if a != b)
    assert transitions >= 6                      # несколько блоков, не сплошная заливка
