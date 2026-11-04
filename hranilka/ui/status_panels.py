"""Постоянные утопленные панели статус-бара (7.4): состояние хранилища,
число записей, индикатор CapsLock, часы. Текущие сообщения статуса
(showMessage) остаются в растягиваемой области слева, размер-grip — справа.

Счётчик записей обновляется по сигналам модели дерева (вставка/удаление/
сброс строк), склеенным в один пересчёт через QTimer(0): отдельных хуков в
дереве не требуется."""
import sys
from datetime import datetime
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QLabel, QStatusBar, QTreeWidget, QTreeWidgetItemIterator

from hranilka.core.nodetypes import LEAF_TYPES

PANEL_PROPERTY = "statusPanel"      # то же имя — в QSS theme.widget_styles
_VK_CAPITAL = 0x14


def caps_lock_on() -> bool:
    """Состояние CapsLock (GetKeyState, только Windows); вне Windows — False."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetKeyState(_VK_CAPITAL) & 1)
    except Exception:                                   # noqa: BLE001 — нет WinAPI
        return False


def vault_state_text(encrypted: bool, locked: bool) -> str:
    if locked:
        return "[X] ЗАБЛОКИРОВАНО"
    return "[#] ШИФР." if encrypted else "[ ] ОТКРЫТО"


def count_text(count: int) -> str:
    return f"Записей: {count}"


def clock_text(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).strftime("%H:%M")


def count_records(tree: QTreeWidget) -> int:
    """Число листовых записей (аккаунты, карты, кошельки, серверы) в дереве."""
    from PySide6.QtCore import Qt
    n = 0
    it = QTreeWidgetItemIterator(tree)
    while it.value() is not None:
        node = it.value().data(0, Qt.ItemDataRole.UserRole)
        if isinstance(node, dict) and node.get("type") in LEAF_TYPES:
            n += 1
        it += 1
    return n


def panel_label(text: str = "", tooltip: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty(PANEL_PROPERTY, "true")
    if tooltip:
        lbl.setToolTip(tooltip)
    return lbl


class StatusPanels(QObject):
    """Панели справа от статусного сообщения. Владелец — главное окно."""

    def __init__(self, statusbar: QStatusBar, tree: QTreeWidget,
                 encrypted: Callable[[], bool], parent: Optional[QObject] = None,
                 caps_reader: Callable[[], bool] = caps_lock_on) -> None:
        super().__init__(parent)
        self._bar = statusbar
        self._tree = tree
        self._encrypted = encrypted
        self._caps_reader = caps_reader
        self._locked = False

        self.state_label = panel_label("", "Состояние хранилища")
        self.count_label = panel_label("", "Количество записей в дереве")
        self.caps_label = panel_label("CAPS", "Caps Lock")
        self.clock_label = panel_label("", "Текущее время")
        for w in (self.state_label, self.count_label, self.caps_label,
                  self.clock_label):
            statusbar.addPermanentWidget(w)
        statusbar.setSizeGripEnabled(True)

        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.setInterval(0)
        self._count_timer.timeout.connect(self.update_count)
        model = tree.model()
        for sig in (model.rowsInserted, model.rowsRemoved, model.modelReset,
                    model.layoutChanged):
            sig.connect(self._schedule_count)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()

        self.update_state()
        self.update_count()
        self._tick()

    # ----- обновление -----

    def _schedule_count(self, *_args: Any) -> None:
        if not self._count_timer.isActive():
            self._count_timer.start()

    def update_count(self) -> None:
        text = count_text(count_records(self._tree))
        if self.count_label.text() != text:
            self.count_label.setText(text)

    def update_state(self) -> None:
        self.state_label.setText(vault_state_text(bool(self._encrypted()),
                                                  self._locked))

    def set_locked(self, locked: bool) -> None:
        self._locked = bool(locked)
        self.update_state()

    def _tick(self) -> None:
        self.update_state()                              # шифрование могли (вы)ключить
        text = clock_text()
        if self.clock_label.text() != text:
            self.clock_label.setText(text)
        self.caps_label.setEnabled(bool(self._caps_reader()))   # CAPS: тускло/ярко

    def stop(self) -> None:
        self._tick_timer.stop()
        self._count_timer.stop()
