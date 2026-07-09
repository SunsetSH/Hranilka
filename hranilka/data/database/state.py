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
                           descending: bool = False) -> list:
        raise NotImplementedError
