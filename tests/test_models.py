"""AccountData: round-trip дат через storage-строки, Qt-free модель."""
import inspect
from datetime import date, datetime

from hranilka.data.models import AccountData
from hranilka.data.models import account_data as account_data_module


def test_model_is_qt_free():
    """Модель данных не должна знать о GUI-фреймворке (backlog-3)."""
    assert "PySide6" not in inspect.getsource(account_data_module)


def test_dates_roundtrip_via_storage():
    d = AccountData()
    d.creation_date = datetime(2026, 7, 4, 14, 30, 5)
    d.password_changed_date = date(2026, 6, 1)
    d.birth_date = date(1990, 12, 31)

    s = d.to_storage()
    # Формат хранения — как у SQLite CURRENT_TIMESTAMP (совместим со старыми БД).
    assert s["fields"]["creation_date"] == "2026-07-04 14:30:05"
    assert s["fields"]["password_changed_date"] == "2026-06-01"
    assert s["personal"]["birth_date"] == "1990-12-31"

    d2 = AccountData.from_storage(s)
    assert d2.creation_date == d.creation_date
    assert d2.password_changed_date == d.password_changed_date
    assert d2.birth_date == d.birth_date


def test_unset_dates_stay_none():
    d = AccountData()
    d.creation_date = None
    s = d.to_storage()
    assert s["fields"]["creation_date"] is None
    d2 = AccountData.from_storage(s)
    assert d2.creation_date is None
    assert d2.password_changed_date is None
    assert d2.birth_date is None


def test_garbage_date_strings_become_none():
    s = AccountData().to_storage()
    s["fields"]["creation_date"] = "мусор"
    s["fields"]["password_changed_date"] = "31-12-1990"   # не тот формат
    d = AccountData.from_storage(s)
    assert d.creation_date is None
    assert d.password_changed_date is None


def test_date_passed_where_datetime_expected():
    """date вместо datetime в creation_date — полночь, а не потеря данных."""
    d = AccountData()
    d.creation_date = date(2026, 7, 4)
    assert d.to_storage()["fields"]["creation_date"] == "2026-07-04 00:00:00"
