"""CopyableDateField: маска дд.мм.гггг[ чч:мм], «не задано», round-trip."""
from PySide6.QtCore import QDate, QDateTime, QTime

from hranilka.ui.widgets import CopyableDateField


# ─── Поле «только дата» ───────────────────────────────────────────────────────

def test_roundtrip_qdate(qapp):
    f = CopyableDateField(is_datetime=False)
    f.set_date(QDate(2026, 7, 4))
    assert f.get_date() == QDateTime(QDate(2026, 7, 4), QTime(0, 0))
    assert f.date_widget.text() == "04.07.2026"


def test_unset_none(qapp):
    f = CopyableDateField(is_datetime=False)
    f.set_date(None)
    assert f.get_date() is None


def test_unknown_type_is_unset(qapp):
    f = CopyableDateField(is_datetime=False)
    f.set_date("2026-07-04")           # строка — неизвестный формат
    assert f.get_date() is None


def test_partial_input_is_unset(qapp):
    """Недописанная дата (маска заполнена не до конца) — «не задано»."""
    f = CopyableDateField(is_datetime=False)
    f.date_widget.setText("04.07.")    # год не введён
    assert f.get_date() is None


def test_invalid_date_is_unset(qapp):
    f = CopyableDateField(is_datetime=False)
    f.date_widget.setText("99.99.2026")
    assert f.get_date() is None


# ─── Поле «дата + время» ──────────────────────────────────────────────────────

def test_roundtrip_qdatetime(qapp):
    f = CopyableDateField(is_datetime=True)
    dt = QDateTime(QDate(2026, 7, 4), QTime(14, 30))
    f.set_date(dt)
    assert f.get_date() == dt
    assert f.date_widget.text() == "04.07.2026 14:30"


def test_datetime_qdate_becomes_midnight(qapp):
    f = CopyableDateField(is_datetime=True)
    f.set_date(QDate(2026, 7, 4))
    assert f.get_date() == QDateTime(QDate(2026, 7, 4), QTime(0, 0))


def test_datetime_without_time_is_midnight(qapp):
    """Дата введена, время пустое — дата не пропадает, время 00:00."""
    f = CopyableDateField(is_datetime=True)
    f.date_widget.setText("04.07.2026")
    assert f.get_date() == QDateTime(QDate(2026, 7, 4), QTime(0, 0))


def test_datetime_time_without_date_is_unset(qapp):
    f = CopyableDateField(is_datetime=True)
    f.date_widget.setText(".. 14:30")
    assert f.get_date() is None


def test_mask_overwrite_and_backspace(qapp):
    """Маска Qt: цифра перезаписывает позицию под курсором (режим Ins),
    Backspace очищает символ — проверяем через реальные key-события."""
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt

    f = CopyableDateField(is_datetime=False)
    f.set_editable(True)
    f.set_date(QDate(2026, 7, 4))      # 04.07.2026

    # Курсор на позицию месяца, печатаем "12" — перезапись, не вставка
    f.date_widget.setCursorPosition(3)
    QTest.keyClicks(f.date_widget, "12")
    assert f.get_date().date() == QDate(2026, 12, 4)

    # Backspace очищает последний символ года → дата недописана → None
    f.date_widget.setCursorPosition(10)
    QTest.keyClick(f.date_widget, Qt.Key_Backspace)
    assert f.get_date() is None
