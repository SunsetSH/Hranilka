"""Общее для моделей данных: версия payload и сериализация галереи."""
from typing import Any, Iterable, Optional

# Текущая версия структуры payload (fin/server). Повышается только при
# несовместимой перестройке; конвертация - в from_storage, а не в БД-миграции.
PAYLOAD_VERSION = 1


def gallery_to_storage(gallery: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Галерея -> формат слоя БД. image_id/blob_size переносятся сквозь кеш
    несохранённых правок (stash -> to_storage -> from_storage -> set_data):
    у ленивых картинок (data=None) они несут «оставить существующий BLOB» и
    размер для лимита общего объёма (контракт H-6/M7-03)."""
    return [{"data": g.get("data"), "desc": g.get("desc", ""),
             "image_id": g.get("image_id"),
             "blob_size": g.get("blob_size")} for g in gallery]


def gallery_from_storage(raw: Optional[Iterable[dict[str, Any]]]
                         ) -> list[dict[str, Any]]:
    return list(raw or [])
