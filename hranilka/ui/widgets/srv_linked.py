"""Совместимость: виджет привязанных серверов живёт в linked.py
(общий LinkedItemsWidget, аудит 2026-11, раздел 8)."""
from hranilka.ui.widgets.linked import (SERVER_TREE_PREFIX, LinkedServersWidget,
                                        server_item_display)

__all__ = ["LinkedServersWidget", "server_item_display", "SERVER_TREE_PREFIX"]
