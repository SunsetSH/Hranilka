"""Аудит 2026-11, widgets/gallery: P4 (потоки, LRU вместо полного BLOB в
элементе, параллельный предпросмотр), drop внешних файлов, реэкспорт
после разбиения на gallery_pipeline / gallery_viewer."""
import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData, QUrl
from PySide6.QtGui import QImage

from hranilka.ui.widgets import gallery as gal
from hranilka.ui.widgets.gallery import GalleryWidget
from hranilka.ui.widgets.gallery_pipeline import ImageBlobCache


@pytest.fixture(autouse=True)
def _silence(monkeypatch):
    monkeypatch.setattr(gal, "_warn", lambda *a, **k: None)


def _png(w=20, h=20):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(0x3366CC)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


# --- реэкспорт ---------------------------------------------------------------

def test_gallery_reexports_old_names():
    from hranilka.ui.widgets.gallery import (  # noqa: F401
        GalleryWidget, _IMAGE_EXTS, _supported_image_exts,
        _downscale_image_bytes, _decode_thumb, _prepare_image_bytes,
        _read_file, _warn, _confirm, FileTooLargeError, QFileDialog,
        QApplication)
    from hranilka.ui.widgets import GalleryWidget as G2
    assert G2 is GalleryWidget
    assert GalleryWidget._decode_image(_png(), bound=10) is not None
    assert GalleryWidget._read_image_size(_png(7, 9)) == (7, 9)


# --- LRU-кэш -----------------------------------------------------------------

def test_blob_cache_evicts_lru_by_total_size():
    c = ImageBlobCache(max_bytes=100)
    c.put(1, b"a" * 40)
    c.put(2, b"b" * 40)
    assert c.get(1) is not None            # 1 теперь самый свежий
    c.put(3, b"c" * 40)                    # вытесняется 2
    assert c.get(2) is None and c.get(1) and c.get(3)
    assert c.total_bytes == 80


def test_blob_cache_rejects_oversized_and_none():
    c = ImageBlobCache(max_bytes=10)
    c.put(1, b"x" * 11)
    c.put(None, b"x")
    c.put(2, None)
    assert len(c) == 0 and c.total_bytes == 0


# --- P4: BLOB не оседает в элементе -----------------------------------------

async def test_preload_keeps_thumb_and_cache_not_item_bytes(qapp):
    data = _png()
    gw = GalleryWidget()
    gw.set_data([{"image_id": 5, "desc": "a", "data": None, "blob_size": len(data)}])
    gw._image_loader = lambda image_id: data
    item = gw.items[0]
    await gw._preload_pipeline([item], gw._preload_gen, None)
    assert item["bytes"] is None
    assert item["thumb_ready"] is True
    assert gw._blob_cache.get(5) == data
    # Контракт H-6: сохранённая картинка уходит как data=None + image_id.
    assert gw.get_data() == [{"desc": "a", "data": None, "image_id": 5,
                              "blob_size": len(data)}]


async def test_click_loads_from_loader_when_evicted(qapp):
    data = _png()
    gw = GalleryWidget()
    gw.set_data([{"image_id": 9, "desc": "", "data": None, "blob_size": 1}])
    calls = []
    gw._image_loader = lambda image_id: calls.append(image_id) or data
    item = gw.items[0]
    assert await gw._load_lazy_bytes_async(item) == data
    assert await gw._load_lazy_bytes_async(item) == data     # из кэша
    assert calls == [9]
    gw._blob_cache.clear()
    assert await gw._load_lazy_bytes_async(item) == data
    assert calls == [9, 9]


async def test_preload_concurrency_is_bounded(qapp):
    data = _png()
    gw = GalleryWidget()
    gw.set_data([{"image_id": i, "desc": "", "data": None} for i in range(1, 11)])
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def loader(image_id):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.03)
        with lock:
            state["now"] -= 1
        return data

    gw._image_loader = loader
    await gw._preload_pipeline(list(gw.items), gw._preload_gen, None)
    assert all(it["thumb_ready"] for it in gw.items)
    assert 1 < state["max"] <= 4


def test_unsaved_bytes_kept_until_assign_saved_ids(qapp):
    data = _png()
    gw = GalleryWidget()
    gw.add_item(data)
    assert gw.items[0]["bytes"] == data
    assert gw.get_data()[0]["data"] == data
    gw.assign_saved_ids([42])
    it = gw.items[0]
    assert it["bytes"] is None and it["image_id"] == 42
    assert it["blob_size"] == len(data)
    assert gw.get_data()[0]["data"] is None and gw.get_data()[0]["image_id"] == 42
    assert gw._local_bytes() == len(data)


def test_set_data_clears_blob_cache(qapp):
    gw = GalleryWidget()
    gw._blob_cache.put(1, b"x")
    gw.set_data([])
    assert len(gw._blob_cache) == 0


def test_in_memory_thumb_decoded_without_loop(qapp):
    gw = GalleryWidget()
    gw.add_item(_png())
    assert gw.items[0]["thumb_ready"] is True
    assert not gw.items[0]["thumb"].pixmap().isNull()


# --- P4: чтение файлов вставки в потоке пула --------------------------------

async def test_paste_file_is_read_off_ui_thread(qapp, monkeypatch, tmp_path):
    data = _png()
    p = tmp_path / "pic.png"
    p.write_bytes(data)
    threads = []

    def fake_read(path, mx):
        threads.append(threading.current_thread())
        return data

    class _Mime:
        def hasImage(self): return False
        def hasUrls(self): return True
        def hasText(self): return False
        def urls(self): return [QUrl.fromLocalFile(str(p))]

    class _Clip:
        def mimeData(self): return _Mime()

    monkeypatch.setattr(gal, "_read_file", fake_read)
    monkeypatch.setattr(gal.QApplication, "clipboard", staticmethod(lambda: _Clip()))
    gw = GalleryWidget()
    await gw._paste_pipeline(gw._data_gen)
    assert threads and threads[0] is not threading.main_thread()
    assert len(gw.items) == 1 and gw.items[0]["bytes"] is not None


# --- Drop внешних файлов ----------------------------------------------------

def _mime_with(paths=(), remote=None):
    m = QMimeData()
    urls = [QUrl.fromLocalFile(str(p)) for p in paths]
    if remote:
        urls.append(QUrl(remote))
    m.setUrls(urls)
    return m


def test_drop_rejected_when_not_editable(qapp, tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(_png())
    gw = GalleryWidget()
    assert gw._drop_acceptable(_mime_with([p])) is False
    gw.import_dropped([str(p)])
    assert gw.items == []


def test_drop_filters_non_images(qapp, tmp_path):
    t = tmp_path / "a.txt"
    t.write_text("x")
    gw = GalleryWidget()
    gw.set_editable(True)
    assert gw._drop_acceptable(_mime_with([t])) is False
    img = tmp_path / "a.png"
    img.write_bytes(_png())
    assert gw._drop_acceptable(_mime_with([img])) is True


async def test_drop_imports_files_through_pipeline(qapp, tmp_path):
    paths = []
    for i in range(2):
        p = tmp_path / f"{i}.png"
        p.write_bytes(_png(10 + i, 10))
        paths.append(str(p))
    gw = GalleryWidget()
    gw.set_editable(True)
    gw.import_dropped(paths)
    await gw.wait_pending_uploads()
    assert len(gw.items) == 2


async def test_drop_respects_count_limit(qapp, tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(_png())
    gw = GalleryWidget()
    gw._MAX_IMAGES_PER_ACCOUNT = 1
    gw.set_editable(True)
    gw.import_dropped([str(p), str(p), str(p)])
    await gw.wait_pending_uploads()
    assert len(gw.items) == 1


def test_drop_remote_url_not_downloaded(qapp, monkeypatch):
    seen = []
    monkeypatch.setattr(gal, "_warn", lambda *a, **k: seen.append(a[2]))
    gw = GalleryWidget()
    gw.set_editable(True)
    gw.import_dropped([], None, remote=True)
    assert gw.items == [] and seen == ["Перетаскивание"]
