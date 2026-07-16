"""Общее состояние Database для mixin-модулей слоя данных.

Все атрибуты создаются в Database.__init__ (database.py); здесь — только
аннотации и заглушки кросс-модульных методов для mypy. DbBase стоит последним
в MRO, поэтому заглушки никогда не перекрывают реальные реализации."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional


class DbBase:
    db_path: str
    conn: Optional[sqlite3.Connection]
    cursor: Optional[sqlite3.Cursor]
    encrypted: bool
    _dek: Optional[bytes]
    _header: Optional[dict]
    _dirty: bool
    _on_dirty: Optional[Callable[[], None]]
    _disk_revision: Optional[tuple[int, int]]
    _lock: Any                       # threading.RLock (фабрика — не тип)
    _executor: Optional[ThreadPoolExecutor]
    _session_gen: int
    _gallery_bytes: Optional[int]

    # Кросс-модульные методы: реализации в соседних mixin'ах (см. database.py).
    def _bump_session(self) -> None:
        raise NotImplementedError

    def invalidate_async_session(self) -> None:
        raise NotImplementedError

    def _commit(self) -> None:
        raise NotImplementedError

    def _mark_dirty(self) -> None:
        raise NotImplementedError

    def _write(self, sql: str, params: tuple = ()) -> None:   # persistence
        raise NotImplementedError

    def _invalidate_gallery_bytes(self) -> None:              # gallery_ops
        raise NotImplementedError

    def _save_gallery_rows(self, account_id: int,             # gallery_ops
                           items: list) -> list:
        raise NotImplementedError

    def _save_gallery_rows_generic(self, table: str, fk_col: str,  # gallery_ops
                                   fk_id: int, items: list) -> list:
        raise NotImplementedError

    def _load_gallery_image(self, table: str, image_id: int):     # gallery_ops
        raise NotImplementedError

    def _next_sort_order(self, table: str, parent_col: str,   # tree_ops
                         parent_id: Optional[int]) -> int:
        raise NotImplementedError

    def _move_fin_item_to_bin_rows(self, item_id: int) -> None:   # fin_items
        raise NotImplementedError

    def _delete_fin_item_rows(self, item_id: int) -> None:        # fin_items
        raise NotImplementedError

    def _set_item_links_rows(self, item_id: int,                  # fin_items
                             account_ids: list) -> None:
        raise NotImplementedError

    def _set_account_fin_links_rows(self, account_id: int,        # fin_items
                                    item_ids: list) -> None:
        raise NotImplementedError

    def _set_account_server_links_rows(self, account_id: int,     # servers
                                       server_ids: list) -> None:
        raise NotImplementedError

    def _add_account_rows(self, service_id: Optional[int],    # tree_ops
                          account_name: str, login: Optional[str] = None,
                          password: Optional[str] = None) -> int:
        raise NotImplementedError

    def _name_maps(self) -> tuple[dict, dict, dict]:          # bulk
        raise NotImplementedError

    def _path_from_maps(self, account_id: int, acc: dict,     # bulk (static)
                        svc: dict, fld: dict) -> str:
        raise NotImplementedError

    def get_tree_structure(self, sort_mode: str = "manual",   # tree_ops
                           descending: bool = False,
                           include_fin: bool = True,
                           include_servers: bool = False) -> list:
        raise NotImplementedError

    def count_fin_items(self) -> int:                         # fin_items
        raise NotImplementedError

    def _move_server_to_bin_rows(self, server_id: int) -> None:   # servers
        raise NotImplementedError

    def _delete_server_rows(self, server_id: int) -> None:        # servers
        raise NotImplementedError

    def get_deleted_servers(self) -> list[dict[str, Any]]:        # servers
        raise NotImplementedError
