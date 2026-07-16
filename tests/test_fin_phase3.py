"""Фаза 3б фин-сущностей: реестр типов (generic-механика на остающихся типах)
и галерея фин-записей (§5).

Тип bank_account и титульник карты CardFaceWidget удалены в итерации 2;
тип ewallet удалён в итерации 3 (m011) — generic-проверки переведены на
crypto_wallet.

MainWindow строится offscreen на временной БД (по образцу test_fin_ui.py):
util.fire без работающего qasync-loop выполняет корутины синхронно.
"""
import pytest

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage

from hranilka.core import nodetypes as nt
from hranilka.core.fin_types import FIN_TYPES, FIELD_KINDS
from hranilka.data.models.fin_item import FinItemData
from hranilka.ui.widgets import GalleryWidget


@pytest.fixture
def window(qapp, tmp_path, monkeypatch, dispose_window):
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    win = main.MainWindow()
    win.config.set("welcome_shown", True)
    win.config.set("show_fin_instruments", True)
    win.apply_config()
    yield win
    dispose_window(win)


def _png_bytes(w=10, h=10):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(0xFF8800)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


# ─── A. Реестр типов (итерация 3: только bank_card и crypto_wallet) ──────────

def test_registry_types():
    """Реестр — ровно bank_card и crypto_wallet; у всех валидные поля и
    короткие подписи кнопок (short_title)."""
    assert set(FIN_TYPES) == {"bank_card", "crypto_wallet"}
    for spec in FIN_TYPES.values():
        assert spec.tabs                               # непустой набор вкладок
        assert spec.short_title                        # подпись кнопки ряда 2
        # Все поля валидны: известный kind и вкладка из tabs.
        for f in spec.fields:
            assert f.kind in FIELD_KINDS
            assert f.tab in spec.tabs


def test_wallet_extract_empty():
    assert FIN_TYPES["crypto_wallet"].extract({"vendor": "x"}) == ("", None)


@pytest.mark.parametrize("type_id", ["crypto_wallet"])
def test_type_payload_roundtrip(qapp, type_id):
    """FinItemTabs типа строится из дескриптора: payload round-trip
    (сеточное поле seed требует валидные BIP-39 слова — исключаем)."""
    from hranilka.ui.fin_tabs import FinItemTabs
    spec = FIN_TYPES[type_id]
    tabs = FinItemTabs(spec)
    tabs.set_all_editable(True)
    skip = {f.key for f in spec.fields if f.kind in ("date", "seed")}
    payload = {f.key: f"v_{f.key}" for f in spec.fields if f.key not in skip}
    payload.update({f.key: "2024-01-15" for f in spec.fields if f.kind == "date"})
    tabs.load_payload(payload)
    out = tabs.collect_payload()
    for key, value in payload.items():
        assert out[key] == value, key


def test_create_buttons_from_registry(window):
    """Кнопки ряда 2 построены из реестра (короткие подписи); удалённых типов
    (bank_account/ewallet) среди них нет."""
    from PySide6.QtWidgets import QPushButton
    texts = [b.text().strip() for b in
             window.fin_buttons_widget.findChildren(QPushButton)]
    assert texts == [f"+ {spec.short_title}" for spec in FIN_TYPES.values()]
    assert not any("СЧЁТ" in t or "ЭЛЕКТРО" in t for t in texts)


@pytest.mark.parametrize("type_id,node_type,prefix", [
    ("crypto_wallet", nt.WALLET, "[₿] "),
])
def test_new_type_node_in_tree(window, type_id, node_type, prefix):
    """Узел нового типа появляется в дереве с ретро-префиксом типа."""
    db = window.db
    sid = db.add_service("S")
    iid = db.add_fin_item(sid, type_id, "Запись")
    window._reload_tree()
    item = window._find_leaf_item(node_type, iid)
    assert item is not None
    assert item.text(0).startswith(prefix)


@pytest.mark.parametrize("type_id,node_type", [
    ("crypto_wallet", nt.WALLET),
])
def test_new_type_create_save_roundtrip(window, monkeypatch, type_id, node_type):
    """Создание → правка → сохранение записи нового типа (без хардкода)."""
    import hranilka.ui.theme as theme_mod
    monkeypatch.setattr(theme_mod, "themed_input", lambda *a, **k: ("Запись", True))

    window.add_fin_record(type_id)
    assert window._current_fin is not None
    assert window._current_fin[0] == node_type
    iid = window._current_fin[1]
    window.db.wait_executor_idle()

    window.tree.setCurrentItem(None)
    window._select_node(node_type, iid)
    assert window.is_editing                       # edit-on-load

    # Первое текстовое поле типа — заполняем и сохраняем.
    key = next(f.key for f in FIN_TYPES[type_id].fields if f.kind == "text")
    window.fin_tabs._widgets[key].set_text("значение")
    window.save_current()

    storage = window.db.load_fin_item(iid)
    assert storage["item_type"] == type_id
    assert storage["payload"][key] == "значение"
    assert not window.is_editing


@pytest.mark.parametrize("type_id,node_type,prefix", [
    ("crypto_wallet", nt.WALLET, "[₿] "),
])
def test_new_type_recycle_bin_restore(window, type_id, node_type, prefix):
    """Новый тип попадает в корзину и восстанавливается через generic-механику."""
    from hranilka.ui.dialogs.recycle_bin import RecycleBinDialog
    db = window.db
    iid = db.add_fin_item(None, type_id, "Запись")
    db.move_fin_item_to_bin(iid)

    dlg = RecycleBinDialog(window.config, db, window)
    assert dlg._list.count() == 1
    assert dlg._list.item(0).text().startswith(prefix)   # префикс из реестра
    dlg._list.setCurrentRow(0)
    dlg._restore()

    assert db.get_deleted_count() == 0
    db.cursor.execute("SELECT deleted_at FROM fin_items WHERE id = ?", (iid,))
    assert db.cursor.fetchone()["deleted_at"] is None


# ─── C. Галерея фин-записей (§5) ──────────────────────────────────────────────

def test_fin_gallery_save_description_and_blob(db):
    """Сохранение галереи карты: описание + BLOB, ленивое чтение по image_id."""
    iid = db.add_fin_item(None, "bank_card", "Карта")
    png = _png_bytes()
    ids = db.save_fin_item(iid, {
        "item_type": "bank_card", "name": "Карта", "payload": {},
        "gallery": [{"desc": "чек", "data": png, "image_id": None}]})
    image_id = ids[0]
    assert image_id is not None
    assert db.load_fin_gallery_image(image_id) == png       # BLOB на месте
    st = db.load_fin_item(iid)
    assert len(st["gallery"]) == 1
    row = st["gallery"][0]
    assert row["desc"] == "чек"
    assert row["data"] is None                              # ленивая загрузка
    assert row["blob_size"] == len(png)


def test_fin_gallery_lazy_keeps_blob(db):
    """Контракт H-6: data=None + image_id НЕ затирает BLOB (обновляет лишь desc)."""
    iid = db.add_fin_item(None, "bank_card", "Карта")
    png = _png_bytes()
    image_id = db.save_fin_item(iid, {
        "item_type": "bank_card", "name": "Карта", "payload": {},
        "gallery": [{"desc": "чек", "data": png, "image_id": None}]})[0]
    # Повторное сохранение ленивого элемента: только описание, BLOB не трогаем.
    db.save_fin_item(iid, {
        "item_type": "bank_card", "name": "Карта", "payload": {},
        "gallery": [{"desc": "новое", "data": None, "image_id": image_id}]})
    assert db.load_fin_gallery_image(image_id) == png       # BLOB не затёрт
    assert db.load_fin_item(iid)["gallery"][0]["desc"] == "новое"


def test_fin_gallery_blob_size_survives_stash():
    """blob_size ленивого элемента переносится сквозь stash (get_data→to_storage)."""
    gw = GalleryWidget()
    gw.set_data([{"image_id": 5, "desc": "x", "data": None, "blob_size": 250}])
    out = gw.get_data()
    assert out[0]["blob_size"] == 250
    assert out[0]["data"] is None and out[0]["image_id"] == 5
    # Round-trip через модель черновика (кеш несохранённых правок).
    fd = FinItemData("bank_card", "Карта")
    fd.gallery = out
    storage = fd.to_storage()
    assert storage["gallery"][0]["blob_size"] == 250


def test_fin_card_wires_gallery_loader(window, monkeypatch):
    """При открытии фин-карточки галерея получает ленивый загрузчик BLOB."""
    import hranilka.ui.theme as theme_mod
    monkeypatch.setattr(theme_mod, "themed_input", lambda *a, **k: ("Карта", True))
    db = window.db
    iid = db.add_fin_item(None, "bank_card", "Карта")
    window._reload_tree()
    window._select_node(nt.CARD, iid)
    gw = window.fin_tabs.f_gallery_widget
    assert gw._image_loader is not None
    assert gw._async_reader is not None


def test_fin_card_saves_gallery_via_window(window, monkeypatch):
    """Полный флоу: добавить картинку в галерею карты в правке и сохранить."""
    import hranilka.ui.theme as theme_mod
    monkeypatch.setattr(theme_mod, "themed_input", lambda *a, **k: ("Карта", True))
    monkeypatch.setattr("hranilka.ui.widgets.gallery._warn", lambda *a, **k: None)
    db = window.db
    iid = db.add_fin_item(None, "bank_card", "Карта")
    window._reload_tree()
    window._select_node(nt.CARD, iid)
    window.edit_current()
    assert window.is_editing

    png = _png_bytes()
    window.fin_tabs.f_gallery_widget.add_item(png, "чек")
    window.save_current()

    st = db.load_fin_item(iid)
    assert len(st["gallery"]) == 1
    assert st["gallery"][0]["desc"] == "чек"
    image_id = st["gallery"][0]["image_id"]
    assert db.load_fin_gallery_image(image_id) == png
