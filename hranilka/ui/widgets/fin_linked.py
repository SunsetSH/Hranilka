"""Совместимость: виджет привязанных финансовых записей живёт в linked.py
(общий LinkedItemsWidget, аудит 2026-11, раздел 8)."""
from hranilka.ui.widgets.linked import (LinkedFinItemsWidget, _node_type_of,
                                        fin_item_display)

__all__ = ["LinkedFinItemsWidget", "fin_item_display", "_node_type_of"]
