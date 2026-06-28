"""Безопасность изображений галереи (Эпик 2): лимиты и устойчивость к мусору.

Требует offscreen-QApplication. Диалоги-предупреждения глушим (monkeypatch),
чтобы модальные окна не блокировали прогон.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtCore import QBuffer, QByteArray, QIODevice

import widgets
from widgets import GalleryWidget


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    QImageReader.setAllocationLimit(256)
    return app


@pytest.fixture(autouse=True)
def _silence_dialogs(monkeypatch):
    # _accept_image/add_item показывают предупреждения — в тестах они не нужны
    # и блокировали бы прогон модальным окном.
    monkeypatch.setattr(widgets, "_warn", lambda *a, **k: None)


def _png_bytes(w, h):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(0xFF8800)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def test_read_size_without_decode(qapp):
    data = _png_bytes(40, 25)
    assert GalleryWidget._read_image_size(data) == (40, 25)
    assert GalleryWidget._read_image_size(b"not an image") is None


def test_accept_valid_image(qapp):
    gw = GalleryWidget()
    assert gw._accept_image(_png_bytes(20, 20)) is True


def test_reject_oversized_bytes(qapp):
    gw = GalleryWidget()
    gw._MAX_IMAGE_BYTES = 10            # искусственно занижаем порог
    assert gw._accept_image(_png_bytes(20, 20)) is False


def test_reject_too_many_pixels(qapp):
    gw = GalleryWidget()
    gw._MAX_IMAGE_PIXELS = 100          # 20x20=400 пикселей > 100
    assert gw._accept_image(_png_bytes(20, 20)) is False


def test_reject_garbage(qapp):
    gw = GalleryWidget()
    assert gw._accept_image(b"definitely not an image") is False


def test_reject_count_limit(qapp):
    gw = GalleryWidget()
    gw._MAX_IMAGES_PER_ACCOUNT = 2
    data = _png_bytes(10, 10)
    gw.add_item(data)
    gw.add_item(data)
    assert gw._accept_image(data) is False


def test_reject_total_bytes_limit(qapp):
    gw = GalleryWidget()
    gw.set_size_context(lambda: gw._MAX_TOTAL_BYTES)   # «база уже полна»
    assert gw._accept_image(_png_bytes(10, 10)) is False


def test_add_item_keeps_bytes_for_corrupt(qapp):
    # Повреждённые байты НЕ теряются: элемент добавляется (placeholder), а данные
    # остаются в get_data(), чтобы сохранение не выкидывало вложение.
    gw = GalleryWidget()
    gw.add_item(b"corrupt image bytes", "чек")
    data = gw.get_data()
    assert len(data) == 1
    assert data[0]["data"] == b"corrupt image bytes"
    assert data[0]["desc"] == "чек"


def test_decode_image_none_for_garbage(qapp):
    assert GalleryWidget._decode_image(b"nope") is None
    assert GalleryWidget._decode_image(_png_bytes(10, 10)) is not None
