"""ServerData — модель карточки VPS-сервера: чистый Python, без Qt.

Оболочка над name и payload — все поля сервера специфичны для сущности и
живут в payload. payload — JSON-полезная нагрузка с версией {"v": 1, ...};
чтение толерантное (dict.get), добавление нового поля не требует ни миграции
БД, ни повышения v (по образцу FinItemData, data/models/fin_item.py). payload
— осознанное исключение из правила «датаклассы, не словари»: произвольная
схема, которую наполняет UI (kv-list виджеты), а не эта модель.

Скаляры payload: hosting, host, ssh_port, os_name, location, paid_until,
price, notes. extra_ips — список строк (толерантное чтение старого формата:
одна строка "IP\nIP..." → splitlines, см. _tolerant_ip_list; УИ §2026-07-15).
Списки: os_users, ssh_keys, panels — толерантны к неполным/мусорным элементам
(см. _tolerant_list). Формат — docs/ТЗ_VPS_Серверы.md §3.
"""
from typing import Any, Optional

# Текущая версия структуры payload. Повышается только при несовместимой
# перестройке; конвертация — здесь (from_storage), а не в БД-миграции.
PAYLOAD_VERSION = 1

# Ключи элементов списковых секций payload (толерантное чтение/запись).
# port/label убраны из панелей (Уточнения UI 2026-07-15) — старый payload с
# ними читается толерантно (_tolerant_list отбрасывает лишние ключи).
OS_USER_KEYS = ("login", "password", "role", "label")
SSH_KEY_KEYS = ("label", "key_type", "public_key", "private_key", "passphrase")
PANEL_KEYS = ("panel_type", "url", "login", "password")


def _tolerant_list(raw: Any, keys: tuple[str, ...]) -> list[dict[str, str]]:
    """Нормализует список словарей: отсутствующие ключи -> "" (толерантное
    чтение старого/неполного payload), лишние ключи отбрасываются, элементы,
    не являющиеся dict, пропускаются. Не-список на входе -> пустой список."""
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            result.append({k: str(item.get(k) or "") for k in keys})
    return result


def _tolerant_ip_list(raw: Any) -> list[str]:
    """Доп. IP: список непустых строк. Толерантно читает старый формат
    (одна строка, IP по строкам — до перехода на список, УИ §2026-07-15);
    не-список и не-строка на входе -> пустой список."""
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if str(v or "").strip()]
    if isinstance(raw, str):
        return [line.strip() for line in raw.splitlines() if line.strip()]
    return []


def _normalize_lists(payload: dict[str, Any]) -> None:
    """Приводит списковые секции payload (включая extra_ips) к толерантной
    форме на месте."""
    payload["os_users"] = _tolerant_list(payload.get("os_users"), OS_USER_KEYS)
    payload["ssh_keys"] = _tolerant_list(payload.get("ssh_keys"), SSH_KEY_KEYS)
    payload["panels"] = _tolerant_list(payload.get("panels"), PANEL_KEYS)
    payload["extra_ips"] = _tolerant_ip_list(payload.get("extra_ips"))


def extract_paid_until(payload: dict[str, Any]) -> Optional[str]:
    """"Оплачен до" из payload — значение экстракт-колонки servers.paid_until
    (маркер [!] в дереве и сортировка без парсинга JSON, §3 ТЗ). Пустое/
    отсутствующее значение -> None."""
    value = payload.get("paid_until")
    text = str(value).strip() if value else ""
    return text or None


class ServerData:
    def __init__(self, name: str = "Новый сервер") -> None:
        self.name = name
        self.payload: dict[str, Any] = {}
        _normalize_lists(self.payload)   # os_users/ssh_keys/panels/extra_ips -> []
        # Галерея — как у FinItemData/AccountData (контракт H-6/M7-03):
        # [{"data": bytes|None, "desc": str, "image_id": int|None,
        #   "blob_size": int|None}].
        self.gallery: list[dict[str, Any]] = []

    # ----- Сериализация для слоя БД -----

    def to_storage(self) -> dict[str, Any]:
        payload = dict(self.payload)
        payload["v"] = PAYLOAD_VERSION
        _normalize_lists(payload)
        return {
            "name": self.name,
            "payload": payload,
            # image_id/blob_size переносятся сквозь кеш несохранённых правок
            # (stash), см. AccountData.to_storage — контракт H-6/M7-03.
            "gallery": [{"data": g.get("data"), "desc": g.get("desc", ""),
                         "image_id": g.get("image_id"),
                         "blob_size": g.get("blob_size")} for g in self.gallery],
        }

    @classmethod
    def from_storage(cls, storage: Optional[dict[str, Any]]) -> "ServerData":
        d = cls()
        if not storage:
            return d
        d.name = storage.get("name") or d.name
        payload = dict(storage.get("payload") or {})
        payload.setdefault("v", PAYLOAD_VERSION)
        _normalize_lists(payload)
        d.payload = payload
        d.gallery = list(storage.get("gallery") or [])
        return d
