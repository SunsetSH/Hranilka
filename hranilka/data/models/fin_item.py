"""FinItemData — модель финансовой записи (карта/кошелёк): чистый Python, без Qt.

Оболочка над item_type, name и payload (тип-специфичные поля). payload —
JSON-полезная нагрузка с версией {"v": 1, ...}; чтение толерантное (dict.get),
добавление поля не требует ни миграции БД, ни повышения v (концепт §1).
payload — осознанное исключение из правила «датаклассы, не словари»: это
произвольная схема, задаваемая дескриптором типа (core/fin_types.py).

Форматы дат внутри payload — те же строковые, что в AccountData
(«ГГГГ-ММ-ДД ЧЧ:ММ:СС» / «ГГГГ-ММ-ДД»), совместимость с БД сохраняется.
"""
from typing import Any, Optional

# Текущая версия структуры payload. Повышается только при несовместимой
# перестройке; конвертация — здесь (from_storage), а не в БД-миграции.
PAYLOAD_VERSION = 1


class FinItemData:
    def __init__(self, item_type: str = "bank_card", name: str = "") -> None:
        self.item_type = item_type
        self.name = name
        self.payload: dict[str, Any] = {}
        # Галерея — как у AccountData: [{"data": bytes|None, "desc": str,
        # "image_id": int|None, "blob_size": int|None}] (контракт H-6/M7-03).
        self.gallery: list[dict[str, Any]] = []

    # ----- Сериализация для слоя БД -----

    def to_storage(self) -> dict[str, Any]:
        payload = dict(self.payload)
        payload["v"] = PAYLOAD_VERSION
        return {
            "item_type": self.item_type,
            "name": self.name,
            "payload": payload,
            # image_id/blob_size переносятся сквозь кеш несохранённых правок
            # (stash), см. AccountData.to_storage — контракт H-6/M7-03.
            "gallery": [{"data": g.get("data"), "desc": g.get("desc", ""),
                         "image_id": g.get("image_id"),
                         "blob_size": g.get("blob_size")} for g in self.gallery],
        }

    @classmethod
    def from_storage(cls, storage: Optional[dict[str, Any]]) -> "FinItemData":
        d = cls()
        if not storage:
            return d
        d.item_type = storage.get("item_type") or "bank_card"
        d.name = storage.get("name") or ""
        d.payload = dict(storage.get("payload") or {})
        d.payload.setdefault("v", PAYLOAD_VERSION)
        d.gallery = list(storage.get("gallery") or [])
        return d
