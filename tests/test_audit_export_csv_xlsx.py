"""Аудит B1/B2: пароли не искажаются, управляющие символы не роняют XLSX."""
import csv

from hranilka.services import export
from hranilka.services.export import Options


def _opts():
    return Options(theme={"font": "Consolas", "font_size": 14,
                          "text_color": "#000000", "main_bg_color": "#F0F0F0",
                          "tree_bg_color": "#FFFFFF"}, title="T")


def _tree(password="=1+1", login="=cmd()", notes="a\x00b\x07c\td\ne"):
    return [{"type": "account", "name": "Акк", "links": [], "children": [],
             "card": {"fields": {"account_name": "Акк", "login": login,
                                 "password": password, "notes": notes},
                      "gallery": []}}]


def test_csv_safe_secret_untouched_default_safe():
    assert export._csv_safe("=x", secret=True) == "=x"
    assert export._csv_safe("=x") == "'=x"
    assert export._csv_safe("ok") == "ok"


def test_csv_password_not_corrupted_login_neutralized(tmp_path):
    out = tmp_path / "e.csv"
    export.export_csv(_tree(), _opts(), str(out))
    with out.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=export.CSV_DELIM))
    header, row = rows[0], rows[1]
    assert row[header.index("Пароль")] == "=1+1"
    assert row[header.index("Логин")] == "'=cmd()"


def test_xlsx_password_stored_verbatim_as_string(tmp_path):
    from openpyxl import load_workbook
    out = tmp_path / "e.xlsx"
    export.export_xlsx(_tree(), _opts(), str(out))
    wb = load_workbook(out)
    ws = wb.active
    header = [c.value for c in ws[1]]
    pw = ws.cell(row=2, column=header.index("Пароль") + 1)
    login = ws.cell(row=2, column=header.index("Логин") + 1)
    assert pw.value == "=1+1" and pw.data_type == "s"
    assert login.value == "=cmd()" and login.data_type == "s"
    wb.close()


def test_xlsx_control_chars_stripped(tmp_path):
    from openpyxl import load_workbook
    out = tmp_path / "e.xlsx"
    export.export_xlsx(_tree(), _opts(), str(out))   # не падает
    wb = load_workbook(out)
    ws = wb.active
    header = [c.value for c in ws[1]]
    notes = ws.cell(row=2, column=header.index("Заметки") + 1).value
    assert notes == "abc\td\ne"
    wb.close()


def test_xlsx_clean_keeps_tab_lf_cr():
    assert export._xlsx_clean("a\tb\nc\rd\x00\x1f\x0b") == "a\tb\nc\rd"
    assert export._xlsx_clean(5) == 5
