"""AccountData — модель карточки аккаунта (примитивы + datetime, БЕЗ Qt).

Даты хранятся стандартными datetime/date; сериализация в строки формата
SQLite CURRENT_TIMESTAMP («ГГГГ-ММ-ДД ЧЧ:ММ:СС») — тот же формат, что и
раньше при Qt-типах, поэтому данные в БД полностью совместимы. Конвертация
в Qt-типы для отображения происходит на границе UI
(hranilka/ui/widgets/fields.py, CopyableDateField)."""
import platform
from datetime import date, datetime, time
from typing import Any, Optional

_DT_FORMAT = "%Y-%m-%d %H:%M:%S"
_DATE_FORMAT = "%Y-%m-%d"


class AccountData:
    def __init__(self) -> None:
        self.name = "Новый аккаунт"
        self.url = ""
        # даты «не заданы» → None; creation_date по умолчанию — момент создания
        self.creation_date: Optional[datetime] = \
            datetime.now().replace(microsecond=0)
        self.password_changed_date: Optional[date] = None
        self.password_change_interval_days: Optional[int] = None
        self.notes = ""
        self.login = ""
        self.password = ""
        self.mobile_phone = ""
        self.first_name = ""
        self.last_name = ""
        self.middle_name = ""
        self.birth_date: Optional[date] = None
        self.address = ""
        self.secret_questions: list[dict[str, str]] = []  # [{"q": "", "a": ""}]
        self.recovery_phrase = ""
        # ID устройства — пользовательские данные, не генерируем фиктивное
        # значение при создании аккаунта.
        self.device_id = ""
        self.one_time_codes: list[str] = []
        self.gallery: list[dict[str, Any]] = []  # [{"data": bytes, "desc": str}]
        self.ip = ""
        self.browser = ""
        self.os = platform.system()
        self.extra_info = ""
        self.linked_accounts: list[int] = []

    # ----- Конвертация дат datetime <-> строка для хранения -----
    # datetime — подкласс date, поэтому isinstance-проверки идут от частного
    # к общему (сначала datetime, затем date).

    @staticmethod
    def _dt_to_str(value: Any) -> Optional[str]:
        if isinstance(value, datetime):
            return value.strftime(_DT_FORMAT)
        if isinstance(value, date):
            return datetime.combine(value, time(0, 0)).strftime(_DT_FORMAT)
        return None

    @staticmethod
    def _date_to_str(value: Any) -> Optional[str]:
        if isinstance(value, datetime):
            return value.date().strftime(_DATE_FORMAT)
        if isinstance(value, date):
            return value.strftime(_DATE_FORMAT)
        return None

    @staticmethod
    def _str_to_dt(value: Any) -> Optional[datetime]:
        """Строка → datetime; None, если значение пусто/некорректно
        (раньше подменялось текущим моментом — ложные данные)."""
        if value:
            try:
                return datetime.strptime(value, _DT_FORMAT)
            except (TypeError, ValueError):
                pass
        return None

    @staticmethod
    def _str_to_date(value: Any) -> Optional[date]:
        """Строка → date; None, если значение пусто/некорректно."""
        if value:
            try:
                return datetime.strptime(value, _DATE_FORMAT).date()
            except (TypeError, ValueError):
                pass
        return None

    # ----- Сериализация для слоя БД (примитивы) -----

    def to_storage(self) -> dict[str, Any]:
        return {
            "fields": {
                "account_name": self.name,
                "url": self.url,
                "login": self.login,
                "password": self.password,
                "creation_date": self._dt_to_str(self.creation_date),
                "password_changed_date": self._date_to_str(self.password_changed_date),
                "password_change_interval_days": self.password_change_interval_days,
                "notes": self.notes,
                "ip": self.ip,
                "browser": self.browser,
                "os": self.os,
                "extra_info": self.extra_info,
            },
            "personal": {
                "mobile_phone": self.mobile_phone,
                "first_name": self.first_name,
                "last_name": self.last_name,
                "middle_name": self.middle_name,
                "birth_date": self._date_to_str(self.birth_date),
                "address": self.address,
            },
            "questions": [{"q": q.get("q", ""), "a": q.get("a", "")} for q in self.secret_questions],
            "recovery": {"phrase": self.recovery_phrase, "device_id": self.device_id},
            "codes": list(self.one_time_codes),
            # image_id сохраняем: он несёт контракт H-6 (data=None + image_id →
            # «оставить существующий BLOB») сквозь кеш несохранённых правок
            # (stash → to_storage → from_storage → set_data). Без него ленивые
            # картинки после стэша пересохранялись бы заново либо терялись.
            # blob_size переносим сквозь кеш правок (M7-03): у ленивых картинок
            # (data=None) он несёт размер BLOB для учёта в лимите общего объёма,
            # иначе после стэша ленивая картинка «весила» бы ноль.
            "gallery": [{"data": g.get("data"), "desc": g.get("desc", ""),
                         "image_id": g.get("image_id"),
                         "blob_size": g.get("blob_size")} for g in self.gallery],
        }

    @classmethod
    def from_storage(cls, storage: Optional[dict[str, Any]]) -> "AccountData":
        d = cls()
        if not storage:
            return d

        f = storage["fields"]
        d.name = f.get("account_name") or ""
        d.url = f.get("url") or ""
        d.login = f.get("login") or ""
        d.password = f.get("password") or ""
        d.creation_date = cls._str_to_dt(f.get("creation_date"))
        d.password_changed_date = cls._str_to_date(f.get("password_changed_date"))
        d.password_change_interval_days = f.get("password_change_interval_days")
        d.notes = f.get("notes") or ""
        d.ip = f.get("ip") or ""
        d.browser = f.get("browser") or ""
        d.os = f.get("os") or ""
        d.extra_info = f.get("extra_info") or ""

        p = storage["personal"]
        d.mobile_phone = p.get("mobile_phone") or ""
        d.first_name = p.get("first_name") or ""
        d.last_name = p.get("last_name") or ""
        d.middle_name = p.get("middle_name") or ""
        d.birth_date = cls._str_to_date(p.get("birth_date"))
        d.address = p.get("address") or ""

        d.secret_questions = list(storage["questions"])
        d.recovery_phrase = storage["recovery"].get("phrase") or ""
        d.device_id = storage["recovery"].get("device_id") or ""
        d.one_time_codes = list(storage["codes"])
        d.gallery = list(storage["gallery"])
        return d
