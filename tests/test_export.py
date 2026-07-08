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


# ─── HTML: галерея карточками «картинка + описание» ──────────────────────────

def _tree_with_gallery(gallery):
    return [{"type": "account", "name": "Акк", "links": [], "children": [],
             "card": {"fields": {"account_name": "Акк"}, "gallery": gallery}}]


def test_html_gallery_cards():
    gallery = [{"data": b"\x89PNG_fake", "desc": "Скрин <главной>"},
               {"data": b"\xff\xd8\xff_fake", "desc": ""}]
    html = export._html_document(_tree_with_gallery(gallery),
                                 Options(theme=_theme(), title="Т"))
    # С описанием — растягиваемая карточка, без — сжатая до картинки (nocap).
    assert html.count("<div class='gitem'>") == 1
    assert html.count("<div class='gitem nocap'>") == 1
    # Описание HTML-экранировано; пустое — не выводится.
    assert html.count("class='cap'") == 1
    assert "Скрин &lt;главной&gt;" in html
    # Несколько колонок; текст обтекает картинку (float), ниже — во всю ширину.
    assert "flex-wrap:wrap" in html
    assert "float:left" in html
    assert "flow-root" in html
    assert f"flex:1 1 {export.HTML_GALLERY_CARD_BASE_PX}px" in html
    assert f"max-width:{export.HTML_GALLERY_IMG_MAX_PX}px" in html


def test_html_gallery_excluded():
    gallery = [{"data": b"\x89PNG_fake", "desc": "x"}]
    html = export._html_document(_tree_with_gallery(gallery),
                                 Options(theme=_theme(), title="Т",
                                         include_gallery=False))
    assert "<div class='gitem" not in html


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
