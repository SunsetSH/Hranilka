"""Импорт резервных кодов 2FA из текстового файла (CodeListWidget.import_codes)
и многоколоночный вывод кодов в HTML-экспорте.
"""
import pytest

import export
from export import Options
# Патч-точки (_warn, QFileDialog) живут в модуле codes пакета widgets (этап 5).
from hranilka.ui.widgets import codes as widgets
from hranilka.ui.widgets.codes import CodeListWidget


# ─── Импорт кодов из файла ────────────────────────────────────────────────────

@pytest.fixture
def codes_widget(qapp):
    w = CodeListWidget()
    w.set_editable(True)
    yield w
    w.deleteLater()


@pytest.fixture
def warnings(monkeypatch):
    """Перехват _warn: тесты headless, модальные окна недопустимы."""
    calls = []
    monkeypatch.setattr(widgets, "_warn",
                        lambda cfg, parent, title, text: calls.append((title, text)))
    return calls


def _pick_file(monkeypatch, path):
    """Файловый диалог «выбирает» подготовленный файл."""
    monkeypatch.setattr(widgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(path), "")))


def test_import_one_code_per_line(codes_widget, warnings, monkeypatch, tmp_path):
    f = tmp_path / "codes.txt"
    f.write_text("AAAA-1111\n\n  BBBB-2222  \nCCCC-3333\n", encoding="utf-8")
    _pick_file(monkeypatch, f)
    codes_widget.import_codes()
    # Пустые строки пропущены, пробелы обрезаны, 1 код — 1 поле.
    assert codes_widget.get_data() == ["AAAA-1111", "BBBB-2222", "CCCC-3333"]
    assert warnings == []


def test_import_appends_to_existing(codes_widget, warnings, monkeypatch, tmp_path):
    codes_widget.add_code("OLD-0000")
    f = tmp_path / "codes.txt"
    f.write_text("NEW-1111", encoding="utf-8")
    _pick_file(monkeypatch, f)
    codes_widget.import_codes()
    assert codes_widget.get_data() == ["OLD-0000", "NEW-1111"]


def test_import_utf8_bom(codes_widget, warnings, monkeypatch, tmp_path):
    f = tmp_path / "codes.txt"
    f.write_bytes("﻿КОД-1\nКОД-2".encode("utf-8"))
    _pick_file(monkeypatch, f)
    codes_widget.import_codes()
    assert codes_widget.get_data() == ["КОД-1", "КОД-2"]


def test_import_cp1251_fallback(codes_widget, warnings, monkeypatch, tmp_path):
    f = tmp_path / "codes.txt"
    f.write_bytes("КОД-1251".encode("cp1251"))
    _pick_file(monkeypatch, f)
    codes_widget.import_codes()
    assert codes_widget.get_data() == ["КОД-1251"]


def test_import_empty_file_warns(codes_widget, warnings, monkeypatch, tmp_path):
    f = tmp_path / "codes.txt"
    f.write_text("\n   \n", encoding="utf-8")
    _pick_file(monkeypatch, f)
    codes_widget.import_codes()
    assert codes_widget.get_data() == []
    assert len(warnings) == 1


def test_import_too_many_lines_aborts(codes_widget, warnings, monkeypatch, tmp_path):
    f = tmp_path / "codes.txt"
    lines = "\n".join(f"C{i}" for i in range(CodeListWidget._MAX_IMPORT_CODES + 1))
    f.write_text(lines, encoding="utf-8")
    _pick_file(monkeypatch, f)
    codes_widget.import_codes()
    # Импорт отменён целиком — частичных состояний нет.
    assert codes_widget.get_data() == []
    assert len(warnings) == 1


def test_import_too_large_file_aborts(codes_widget, warnings, monkeypatch, tmp_path):
    f = tmp_path / "codes.txt"
    f.write_bytes(b"A" * (CodeListWidget._MAX_IMPORT_BYTES + 1))
    _pick_file(monkeypatch, f)
    codes_widget.import_codes()
    assert codes_widget.get_data() == []
    assert len(warnings) == 1


def test_import_cancelled_dialog_noop(codes_widget, warnings, monkeypatch):
    monkeypatch.setattr(widgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    codes_widget.import_codes()
    assert codes_widget.get_data() == []
    assert warnings == []


# ─── HTML: коды в несколько колонок ──────────────────────────────────────────

def _tree_with_codes(codes):
    return [{"type": "account", "name": "Акк", "links": [], "children": [],
             "card": {"fields": {"account_name": "Акк"}, "codes": codes}}]


def test_html_codes_multicolumn():
    codes = ["AAAA-1111", "BB", "<b>инъекция</b>"]
    html = export._html_document(_tree_with_codes(codes),
                                 Options(title="Т", theme={}))
    # Потолок колонок задан в CSS, минимальная ширина — по самому длинному коду.
    assert f"column-count:{export.HTML_CODES_MAX_COLS}" in html
    width = max(len(c) for c in codes) + 2
    assert f"<div class='codes' style='column-width:{width}ch'>" in html
    # 1 код — 1 div; HTML экранирован.
    assert "<div>AAAA-1111</div>" in html
    assert "<div>BB</div>" in html
    assert "&lt;b&gt;инъекция&lt;/b&gt;" in html
    assert "<b>инъекция</b>" not in html


def test_html_no_codes_no_block():
    html = export._html_document(_tree_with_codes([]),
                                 Options(title="Т", theme={}))
    assert "class='codes'" not in html
