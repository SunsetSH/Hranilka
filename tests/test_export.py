"""Экспорт: форматы дают непустой файл; нейтрализация формул CSV/XLSX (M-10).

PDF удалён в 0.4 — FORMATS должен содержать только txt/csv/xlsx/html.
"""
import export
from export import Options


def _theme():
    return {"font": "Consolas", "font_size": 14, "text_color": "#000000",
            "main_bg_color": "#F0F0F0", "tree_bg_color": "#FFFFFF"}


def _tree(db):
    sid = db.add_service("Сервис")
    db.add_account(sid, "Акк1", login="user", password="pass")
    return db.export_subtree()


def test_formats_set():
    assert set(export.FORMATS) == {"txt", "csv", "xlsx", "html"}
    assert "pdf" not in export.FORMATS


def test_all_formats_produce_files(db, tmp_path):
    tree = _tree(db)
    opts = Options(theme=_theme(), title="Тест")
    for fmt, (fn, ext, _flt) in export.FORMATS.items():
        out = tmp_path / ("exp" + ext)
        fn(tree, opts, str(out))
        assert out.exists() and out.stat().st_size > 0, fmt


def test_csv_formula_injection_neutralized():
    # значения, начинающиеся с = + - @ и т.п., должны префиксоваться апострофом.
    for danger in ("=cmd()", "+1", "-1", "@x", "\tx"):
        assert export._csv_safe(danger).startswith("'")
    assert export._csv_safe("normal") == "normal"


# ─── L-15: экспорт пустого дерева и «пустого» аккаунта ───────────────────────

def test_export_empty_tree_no_crash(db, tmp_path):
    """Экспорт базы без аккаунтов не падает и создаёт файл во всех форматах."""
    tree = db.export_subtree()               # пустая база → []
    assert tree == []
    opts = Options(theme=_theme(), title="Пусто")
    for fmt, (fn, ext, _flt) in export.FORMATS.items():
        out = tmp_path / ("empty" + ext)
        fn(tree, opts, str(out))
        assert out.exists(), fmt             # файл создан (может быть с заголовком)


def test_export_account_with_empty_fields(db, tmp_path):
    """Аккаунт с password=None и пустыми полями экспортируется без исключений."""
    sid = db.add_service("Сервис")
    # login/password не заданы → None; прочие поля тоже пустые.
    db.add_account(sid, "ПустойАкк")
    tree = db.export_subtree()
    opts = Options(theme=_theme(), title="Тест")
    for fmt, (fn, ext, _flt) in export.FORMATS.items():
        out = tmp_path / ("nulls" + ext)
        fn(tree, opts, str(out))
        assert out.exists() and out.stat().st_size > 0, fmt
