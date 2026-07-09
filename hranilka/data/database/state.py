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
