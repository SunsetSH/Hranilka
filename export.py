"""Экспорт данных Хранилки в человекочитаемые/переносимые форматы.

Форматы: TXT (блокнот), CSV (таблица для Excel/переноса), HTML (оформленный
документ с картинками), XLSX (книга Excel). На вход — структура из
Database.export_subtree(): список узлов дерева, где у каждого узла
type=='account' есть ключи 'card' (как load_account) и 'links'.

ВНИМАНИЕ: экспорт сохраняет выбранные данные в ОТКРЫТОМ виде. Включение паролей
и секретов управляется флагами Options.

Модуль не зависит от Qt (XLSX тянет openpyxl, импорт изолирован внутри функции).
"""
import base64
import csv
from datetime import datetime
from html import escape as esc
from pathlib import Path


# Префиксы-«иконки» элементов дерева (как в главном окне).
PREFIX = {"folder": "[+] ", "service": "[o] ", "account": "(i) "}

# Группы полей карточки (для галочек «Что включить»).
# BASIC  — вкладки «База» + «Логин и пароль».
# OTHER  — все остальные вкладки (ПД, секр. вопросы, фраза, 2FA, технические).
# Галерея (картинки) выгружается отдельно (include_gallery) — только в HTML.
GROUP_BASIC = [
    "Название аккаунта", "Адрес сайта (URL)", "Дата создания",
    "Логин", "Пароль", "Пароль сменён", "Сменять пароль каждые",
    "Заметки", "Связанные аккаунты",
]
GROUP_OTHER = [
    "Имя", "Фамилия", "Отчество", "Дата рождения", "Адрес",
    "Секретные вопросы", "Фраза восстановления", "ID устройства",
    "Резервные коды 2FA", "IP адрес", "Браузер", "ОС",
]

CSV_DELIM = ";"  # разделитель для русского Excel


class Options:
    """Параметры экспорта.

    include_basic   — базовые данные, логин и пароль (вкладки «База»/«Логин»).
    include_other   — остальные поля (ПД, секр. вопросы, фраза, 2FA, технические).
    include_gallery — галерея (изображения и описания); только HTML.
    theme — словарь цветов/шрифта для HTML."""

    def __init__(self, include_basic=True, include_other=True,
                 include_gallery=True, title="Вся база", theme=None):
        self.include_basic = include_basic
        self.include_other = include_other
        self.include_gallery = include_gallery
        self.title = title
        self.theme = theme or {}

    def _group_enabled(self, label):
        if label in GROUP_BASIC:
            return self.include_basic
        if label in GROUP_OTHER:
            return self.include_other
        return True


# ─── Общий слой: поля карточки ───────────────────────────────────────────────

def _s(v):
    return "" if v is None else str(v)


def _csv_safe(value):
    """Нейтрализует formula injection в табличных форматах (CSV/XLSX).

    Excel/openpyxl трактуют значение, начинающееся с = + - @ (или с управляющего
    символа перед ними), как формулу — вплоть до DDE/WEBSERVICE-вызовов при
    открытии файла. Префиксуем такие значения апострофом: Excel покажет текст
    как есть и формулу не выполнит. На HTML/TXT не влияет (там не вызывается)."""
    if value and value[0] in ("=", "+", "-", "@", "\t", "\r", "\n"):
        return "'" + value
    return value


def _account_rows(card, links, opts, skip_empty=True):
    """Упорядоченный список (подпись, значение) для одной карточки.

    Поля выключённых групп (basic/other) полностью пропускаются. skip_empty=True
    — пропускать также пустые поля включённых групп (TXT/HTML); False — оставлять
    их пустыми (CSV, фиксированные колонки)."""
    f = card.get("fields", {})
    p = card.get("personal", {})
    rows = []

    def add(label, value):
        if not opts._group_enabled(label):
            return
        value = _s(value)
        if value == "" and skip_empty:
            return
        rows.append((label, value))

    add("Название аккаунта", f.get("account_name"))
    add("Адрес сайта (URL)", f.get("url"))
    add("Дата создания", f.get("creation_date"))
    add("Логин", f.get("login"))
    add("Пароль", f.get("password"))
    add("Пароль сменён", f.get("password_changed_date"))
    iv = f.get("password_change_interval_days")
    add("Сменять пароль каждые", f"{iv} дн." if iv else "")
    add("Заметки", f.get("notes"))
    add("Связанные аккаунты", "\n".join(l["name"] for l in links) if links else "")

    add("Имя", p.get("first_name"))
    add("Фамилия", p.get("last_name"))
    add("Отчество", p.get("middle_name"))
    add("Дата рождения", p.get("birth_date"))
    add("Адрес", p.get("address"))

    q_lines = []
    for q in card.get("questions", []):
        qt = _s(q.get("q")).strip()
        at = _s(q.get("a"))
        if not qt and not at:
            continue
        q_lines.append(f"{qt} — {at}" if qt else at)
    add("Секретные вопросы", "\n".join(q_lines))

    add("Фраза восстановления", card.get("recovery", {}).get("phrase"))
    add("ID устройства", card.get("recovery", {}).get("device_id"))

    codes = [_s(c) for c in card.get("codes", []) if _s(c).strip()]
    add("Резервные коды 2FA", "\n".join(codes))

    add("IP адрес", f.get("ip"))
    add("Браузер", f.get("browser"))
    add("ОС", f.get("os"))

    return rows


# ─── Картинки ────────────────────────────────────────────────────────────────

def _img_ext(data):
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"\x89PNG":
        return ".png"
    if data[:3] == b"GIF":
        return ".gif"
    if data[:2] == b"BM":
        return ".bmp"
    return ".png"


def _img_mime(data):
    return {".jpg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".bmp": "image/bmp"}[_img_ext(data)]


def _gallery(node):
    """Картинки аккаунта с непустыми данными."""
    return [g for g in node["card"].get("gallery", []) if g.get("data") is not None]


# ─── TXT ─────────────────────────────────────────────────────────────────────

def export_txt(tree, opts, path):
    path = Path(path)
    bar = "=" * 60
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [bar, " ХРАНИЛКА — ЭКСПОРТ", f" {opts.title}    {now}", bar, ""]

    def account(node, indent):
        for label, value in _account_rows(node["card"], node.get("links"), opts):
            if "\n" in value:
                lines.append(f"{indent}{label}:")
                for vl in value.split("\n"):
                    lines.append(f"{indent}    {vl}")
            else:
                lines.append(f"{indent}{label}: {value}")
        # Картинки в текст не помещаются — отмечаем только их наличие
        # (как часть «остальных полей»).
        gal = _gallery(node)
        if gal and opts.include_other:
            lines.append(f"{indent}[Галерея: {len(gal)} изобр. — доступно в HTML]")
        lines.append(f"{indent}{'-' * 40}")

    def walk(node, depth):
        indent = "    " * depth
        lines.append(f"{indent}{PREFIX[node['type']]}{node['name']}")
        if node["type"] == "account":
            account(node, indent + "    ")
        for child in node.get("children", []):
            walk(child, depth + 1)

    for node in tree:
        walk(node, 0)

    path.write_text("\n".join(lines), encoding="utf-8")


# ─── CSV ─────────────────────────────────────────────────────────────────────

def export_csv(tree, opts, path):
    # Колонки зависят от включённых групп. Колонка «Картинок» (количество
    # вложений) относится к «остальным полям» — выводится только с include_other.
    cols, header = _table_columns(opts)
    out = []

    def walk(node, prefix):
        name = node["name"]
        if node["type"] == "account":
            out.append(_table_row(node, prefix + [name], cols, opts))
        for child in node.get("children", []):
            walk(child, prefix + [name])

    for node in tree:
        walk(node, [])

    # utf-8-sig (BOM) — чтобы Excel корректно открыл кириллицу.
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=CSV_DELIM)
        w.writerow(header)
        w.writerows(out)


def _table_columns(opts):
    """Колонки таблицы (CSV/XLSX) по включённым группам. Возвращает (cols, header),
    где cols — поля карточки, header — полная строка заголовка с «Путь»/«Картинок»."""
    cols = []
    if opts.include_basic:
        cols += GROUP_BASIC
    if opts.include_other:
        cols += GROUP_OTHER
    header = ["Путь"] + cols + (["Картинок"] if opts.include_other else [])
    return cols, header


def _table_row(node, path_parts, cols, opts):
    """Строка таблицы для одного аккаунта (CSV/XLSX)."""
    d = dict(_account_rows(node["card"], node.get("links"), opts, skip_empty=False))
    row = [" / ".join(path_parts)] + [d.get(l, "") for l in cols]
    if opts.include_other:
        row.append(str(len(_gallery(node))))
    # Нейтрализуем формулы в каждой ячейке (CSV и XLSX используют эту строку).
    return [_csv_safe(_s(c)) for c in row]


# ─── HTML ────────────────────────────────────────────────────────────────────

def _default_img_tag(data):
    src = f"data:{_img_mime(data)};base64," + base64.b64encode(data).decode("ascii")
    return f"<img class='shot' src='{src}'>"


def _html_document(tree, opts, render_img=_default_img_tag):
    th = opts.theme
    font = th.get("font", "Consolas")
    fs = int(th.get("font_size", 14))
    text = th.get("text_color", "#000000")
    main_bg = th.get("main_bg_color", "#F0F0F0")
    tree_bg = th.get("tree_bg_color", "#FFFFFF")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    p = []
    p.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    p.append("<title>Хранилка — экспорт</title><style>")
    p.append(f"body{{background:{main_bg};color:{text};"
             f"font-family:'{font}',monospace;font-size:{fs}px;margin:24px;}}")
    p.append(f".banner{{border:2px solid #808080;background:{tree_bg};"
             f"padding:12px 16px;margin-bottom:20px;}}")
    p.append(f".banner h1{{margin:0;font-size:{fs + 8}px;letter-spacing:2px;}}")
    p.append(f"h1.folder{{font-size:{fs + 6}px;border-bottom:2px solid #808080;margin-top:24px;}}")
    p.append(f"h2.service{{font-size:{fs + 3}px;margin-left:12px;}}")
    p.append(f".account{{border:2px inset #808080;background:{tree_bg};"
             f"margin:10px 0 10px 24px;padding:8px 12px;}}")
    p.append(f".account h3{{margin:0 0 6px 0;font-size:{fs + 2}px;}}")
    p.append("table.card{border-collapse:collapse;width:100%;}")
    p.append("table.card td{border:1px solid #808080;padding:4px 8px;vertical-align:top;}")
    p.append("td.label{font-weight:bold;white-space:nowrap;width:200px;}")
    p.append("td.value{white-space:pre-wrap;}")
    p.append(".gallery{margin-top:8px;}")
    p.append("img.shot{max-width:480px;border:2px outset #808080;margin:6px 0;display:block;}")
    p.append(".cap{font-style:italic;margin-bottom:6px;}")
    p.append("</style></head><body>")
    p.append(f"<div class='banner'><h1>ХРАНИЛКА — ЭКСПОРТ</h1>"
             f"{esc(opts.title)} &nbsp;&nbsp; {now}</div>")

    def account(node):
        p.append("<div class='account'>")
        p.append(f"<h3>(i) {esc(node['name'])}</h3><table class='card'>")
        for label, value in _account_rows(node["card"], node.get("links"), opts):
            p.append(f"<tr><td class='label'>{esc(label)}</td>"
                     f"<td class='value'>{esc(value)}</td></tr>")
        p.append("</table>")
        if opts.include_gallery:
            gal = _gallery(node)
            if gal:
                p.append("<div class='gallery'>")
                for g in gal:
                    p.append(render_img(g["data"]))
                    desc = esc(_s(g.get("desc")))
                    if desc:
                        p.append(f"<div class='cap'>{desc}</div>")
                p.append("</div>")
        p.append("</div>")

    def walk(node):
        t = node["type"]
        if t == "folder":
            p.append(f"<h1 class='folder'>[+] {esc(node['name'])}</h1>")
        elif t == "service":
            p.append(f"<h2 class='service'>[o] {esc(node['name'])}</h2>")
        elif t == "account":
            account(node)
        for child in node.get("children", []):
            walk(child)

    for node in tree:
        walk(node)
    p.append("</body></html>")
    return "".join(p)


def export_html(tree, opts, path):
    Path(path).write_text(_html_document(tree, opts), encoding="utf-8")


# ─── XLSX ────────────────────────────────────────────────────────────────────

# Ширина столбца XLSX в символах ≈ (px - 5) / 7. 400 px ≈ 56 символов.
_XLSX_MAX_WIDTH = 56


def export_xlsx(tree, opts, path):
    # Импорт openpyxl изолирован: остальные форматы не требуют этой зависимости.
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    cols, header = _table_columns(opts)
    rows = []

    def walk(node, prefix):
        name = node["name"]
        if node["type"] == "account":
            rows.append(_table_row(node, prefix + [name], cols, opts))
        for child in node.get("children", []):
            walk(child, prefix + [name])

    for node in tree:
        walk(node, [])

    wb = Workbook()
    ws = wb.active
    ws.title = "Хранилка"
    ws.append(header)
    for row in rows:
        ws.append(row)

    # Только чёрный текст; выравнивание влево, перенос строк. Цвета темы
    # игнорируются (XLSX — для данных, не для оформления).
    black = Font(color="FF000000")
    black_bold = Font(color="FF000000", bold=True)
    align = Alignment(horizontal="left", vertical="top", wrap_text=True)
    for r, row_cells in enumerate(ws.iter_rows(), start=1):
        for cell in row_cells:
            cell.alignment = align
            cell.font = black_bold if r == 1 else black

    # Ширина столбца — по самому длинному фрагменту текста, но не более 400 px.
    for c, title in enumerate(header, start=1):
        longest = len(str(title))
        for row in rows:
            value = row[c - 1] if c - 1 < len(row) else ""
            for line in str(value).split("\n"):
                longest = max(longest, len(line))
        letter = ws.cell(row=1, column=c).column_letter
        ws.column_dimensions[letter].width = min(longest + 2, _XLSX_MAX_WIDTH)

    wb.save(path)


# Соответствие формата → (функция, расширение, фильтр QFileDialog).
FORMATS = {
    "txt": (export_txt, ".txt", "Текстовый файл (*.txt)"),
    "csv": (export_csv, ".csv", "CSV таблица (*.csv)"),
    "xlsx": (export_xlsx, ".xlsx", "Книга Excel (*.xlsx)"),
    "html": (export_html, ".html", "HTML документ (*.html)"),
}
