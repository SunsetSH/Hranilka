import uuid
import platform
from PySide6.QtCore import QDateTime, QDate, QTime

_DT_FORMAT = "yyyy-MM-dd HH:mm:ss"
_DATE_FORMAT = "yyyy-MM-dd"


class AccountData:
    def __init__(self):
        self.name = "Новый аккаунт"
        self.url = ""
        self.creation_date = QDateTime.currentDateTime()  # момент создания — осмысленный дефолт
        self.password_changed_date = None                  # «не задано», а не сегодня
        self.password_change_interval_days = None
        self.notes = ""
        self.login = ""
        self.password = ""
        self.first_name = ""
        self.last_name = ""
        self.middle_name = ""
        self.birth_date = None                             # «не задано», а не сегодня
        self.address = ""
        self.secret_questions = []  # [{"q": "", "a": ""}]
        self.recovery_phrase = ""
        self.device_id = str(uuid.uuid4())[:8].upper()
        self.one_time_codes = []
        self.gallery = []  # [{"data": bytes, "desc": str}]
        self.ip = ""
        self.browser = ""
        self.os = platform.system()
        self.extra_info = ""
        self.linked_accounts = []

    # ----- Конвертация дат Qt <-> строка для хранения -----

    @staticmethod
    def _dt_to_str(value):
        if isinstance(value, QDateTime):
            return value.toString(_DT_FORMAT)
        if isinstance(value, QDate):
            return QDateTime(value, QTime(0, 0)).toString(_DT_FORMAT)
        return None

    @staticmethod
    def _date_to_str(value):
        if isinstance(value, QDateTime):
            return value.date().toString(_DATE_FORMAT)
        if isinstance(value, QDate):
            return value.toString(_DATE_FORMAT)
        return None

    @staticmethod
    def _str_to_dt(value):
        """Строка → QDateTime; None, если значение пусто/некорректно
        (раньше подменялось текущим моментом — ложные данные)."""
        if value:
            dt = QDateTime.fromString(value, _DT_FORMAT)
            if dt.isValid():
                return dt
        return None

    @staticmethod
    def _str_to_date(value):
        """Строка → QDate; None, если значение пусто/некорректно."""
        if value:
            d = QDate.fromString(value, _DATE_FORMAT)
            if d.isValid():
                return d
        return None

    # ----- Сериализация для слоя БД (примитивы) -----

    def to_storage(self):
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
                "first_name": self.first_name,
                "last_name": self.last_name,
                "middle_name": self.middle_name,
                "birth_date": self._date_to_str(self.birth_date),
                "address": self.address,
            },
            "questions": [{"q": q.get("q", ""), "a": q.get("a", "")} for q in self.secret_questions],
            "recovery": {"phrase": self.recovery_phrase, "device_id": self.device_id},
            "codes": list(self.one_time_codes),
            "gallery": [{"data": g.get("data"), "desc": g.get("desc", "")} for g in self.gallery],
        }

    @classmethod
    def from_storage(cls, storage):
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
        d.first_name = p.get("first_name") or ""
        d.last_name = p.get("last_name") or ""
        d.middle_name = p.get("middle_name") or ""
        d.birth_date = cls._str_to_date(p.get("birth_date"))
        d.address = p.get("address") or ""

        d.secret_questions = list(storage["questions"])
        d.recovery_phrase = storage["recovery"].get("phrase") or ""
        d.device_id = storage["recovery"].get("device_id") or d.device_id
        d.one_time_codes = list(storage["codes"])
        d.gallery = list(storage["gallery"])
        return d
