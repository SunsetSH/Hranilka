"""Пиксельные иконки дерева (7.5): рисунки, кэш, режимы дерева, sanitize."""
import json

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTreeWidget

from hranilka.core import config as config_mod
from hranilka.core.nodetypes import ACCOUNT, CARD, FOLDER, SERVER, SERVICE, WALLET
from hranilka.ui import pixel_icons
from hranilka.ui.tree import TreeMixin

TEXT = "#112233"
BG = "#FFEEDD"


def test_art_is_16x16_with_known_palette():
    assert set(pixel_icons.kinds()) >= {FOLDER, SERVICE, ACCOUNT, CARD, WALLET,
                                        SERVER, pixel_icons.FAVORITE}
    for kind, art in pixel_icons.ICON_ART.items():
        assert len(art) == 16, kind
        for row in art:
            assert len(row) == 16 and set(row) <= set(".of"), kind


@pytest.mark.parametrize("kind", list(pixel_icons.ICON_ART))
def test_render_size_and_outline_color(qapp, kind):
    fill = pixel_icons.fill_color(TEXT, BG)
    pm = pixel_icons.render(kind, TEXT, fill)
    assert (pm.width(), pm.height()) == (16, 16)
    img = pm.toImage()
    for y, row in enumerate(pixel_icons.ICON_ART[kind]):
        for x, ch in enumerate(row):
            px = QColor.fromRgba(img.pixel(x, y))
            if ch == "o":
                assert px.name() == TEXT and px.alpha() == 255
            elif ch == "f":
                assert px.name() == QColor(fill).name()
            else:
                assert px.alpha() == 0


def test_render_cache_and_dpr(qapp):
    a = pixel_icons.render(FOLDER, TEXT, BG)
    assert pixel_icons.render(FOLDER, TEXT, BG) is a
    assert pixel_icons.render(FOLDER, "#000000", BG) is not a
    x2 = pixel_icons.render(FOLDER, TEXT, BG, 2.0)
    assert x2 is not a and x2.width() == 32 and x2.devicePixelRatio() == 2


class _Cfg:
    def __init__(self, **kw):
        self.d = {"text_color": TEXT, "tree_bg_color": BG, **kw}

    def get(self, key, default=None):
        return self.d.get(key, default)


class _Host(TreeMixin):
    def __init__(self, qapp, icons):
        self.tree = QTreeWidget()
        self.config = _Cfg(tree_icons=icons)
        self._dirty_ids = set()


def _nodes():
    return {"type": FOLDER, "id": 1, "name": "Папка", "children": [
        {"type": ACCOUNT, "id": 2, "name": "Лист в папке", "is_favorite": True},
        {"type": SERVICE, "id": 3, "name": "Сервис", "children": [
            {"type": ACCOUNT, "id": 4, "name": "Акк"}]},
        {"type": CARD, "id": 5, "name": "Карта"},
    ]}


def test_pixel_mode_icon_without_prefix(qapp):
    h = _Host(qapp, "pixel")
    top = h._add_tree_node(h.tree, _nodes())
    for it in h._iter_items():
        node = h._node(it)
        assert not it.icon(0).isNull()
        assert it.text(0) == node["name"]          # без префикса и «* »
    assert top.child(0).childCount() == 0         # лист в папке тоже с иконкой


def test_text_mode_unchanged(qapp):
    h = _Host(qapp, "text")
    top = h._add_tree_node(h.tree, _nodes())
    assert top.text(0) == "[+] Папка"
    assert top.child(0).text(0) == "* (i) Лист в папке"
    assert all(it.icon(0).isNull() for it in h._iter_items())


def test_mode_switch_refresh_keeps_node_names(qapp):
    h = _Host(qapp, "text")
    top = h._add_tree_node(h.tree, _nodes())
    h.config.d["tree_icons"] = "pixel"
    h.refresh_tree_icons()
    assert top.text(0) == "Папка" and not top.icon(0).isNull()
    assert h._node(top)["name"] == "Папка"         # имя для поиска/F2 — из UserRole
    h.config.d["tree_icons"] = "text"
    h.refresh_tree_icons()
    assert top.text(0) == "[+] Папка" and top.icon(0).isNull()


def test_config_sanitize_tree_icons(tmp_path, monkeypatch):
    f = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_FILE", f)
    assert config_mod.Config().get("tree_icons") == "text"
    f.write_text(json.dumps({"tree_icons": "pixel"}), encoding="utf-8")
    assert config_mod.Config().get("tree_icons") == "pixel"
    f.write_text(json.dumps({"tree_icons": "bogus"}), encoding="utf-8")
    assert config_mod.Config().get("tree_icons") == "text"


def _bbox(kind):
    cells = [(x, y) for y, row in enumerate(pixel_icons.ICON_ART[kind])
             for x, ch in enumerate(row) if ch != "."]
    xs, ys = [c[0] for c in cells], [c[1] for c in cells]
    return max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, len(cells)


def test_favorite_star_is_symmetric():
    art = pixel_icons.ICON_ART[pixel_icons.FAVORITE]
    assert all(row[:15] == row[:15][::-1] for row in art)


def test_fin_and_server_icons_differ_by_silhouette():
    card_w, card_h, card_area = _bbox(CARD)
    wallet_w, wallet_h, wallet_area = _bbox(WALLET)
    server_w, server_h, _ = _bbox(SERVER)
    assert card_w > card_h * 1.2           # широкая карта
    assert server_h > server_w             # высокая стойка
    assert wallet_area < wallet_w * wallet_h - 20   # скруглённая монета
    assert "f" not in "".join(pixel_icons.ICON_ART[WALLET])   # без фона под ₿
    assert card_area == card_w * card_h    # карта — сплошной прямоугольник
