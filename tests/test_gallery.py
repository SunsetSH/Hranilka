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


def _gradient_png(w, h):
    """PNG с градиентом — заметного объёма (в отличие от сплошной заливки),
    чтобы downscale реально давал выигрыш в байтах."""
    from PySide6.QtGui import QPainter, QLinearGradient, QColor
    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    grad = QLinearGradient(0, 0, w, h)
    grad.setColorAt(0.0, QColor(255, 0, 0))
    grad.setColorAt(0.5, QColor(0, 255, 0))
    grad.setColorAt(1.0, QColor(0, 0, 255))
    p.fillRect(0, 0, w, h, grad)
    p.end()
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


# ─── Баг 3: сжатие больших изображений при импорте ──────────────────────────

def test_downscale_reduces_large_image(qapp):
    from widgets import _downscale_image_bytes
    big = _gradient_png(3000, 2000)         # длинная сторона > 2560, объёмный PNG
    out = _downscale_image_bytes(big, 2560, 90)
    assert out is not None
    w, h = GalleryWidget._read_image_size(out)
    assert max(w, h) <= 2560
    assert len(out) < len(big)


def test_downscale_skips_small_image(qapp):
    from widgets import _downscale_image_bytes
    small = _png_bytes(800, 600)            # в пределах — не трогаем
    assert _downscale_image_bytes(small, 2560, 90) is None


# ─── Баг 6: строки/элементы создаются с родителем (не мелькают как окна) ─────

def test_gallery_item_has_parent(qapp):
    gw = GalleryWidget()
    gw.add_item(_png_bytes(10, 10))
    assert gw.items[0]["widget"].parent() is gw


def test_secret_question_row_has_parent(qapp):
    from widgets import SecretQuestionsWidget
    w = SecretQuestionsWidget()
    w.add_row("q", "a")
    row_widget = w.rows[0][0].parent()
    assert row_widget.parent() is w


# ─── Баг 4: пустые строки вопросов/кодов не сохраняются ─────────────────────

def test_secret_questions_skip_empty(qapp):
    from widgets import SecretQuestionsWidget
    w = SecretQuestionsWidget()
    w.set_editable(True)
    w.add_row("q1", "a1")
    w.add_row("", "")          # обе пустые → не сохраняется
    w.add_row("q3", "")        # вопрос есть → сохраняется
    data = w.get_data()
    assert data == [{"q": "q1", "a": "a1"}, {"q": "q3", "a": ""}]
    # после set_data отфильтрованным набором пустых строк в UI не остаётся
    w.set_data(data)
    assert len(w.rows) == 2


def test_codes_skip_empty(qapp):
    from widgets import CodeListWidget
    w = CodeListWidget()
    w.set_editable(True)
    w.add_code("abc")
    w.add_code("")
    w.add_code("   ")
    assert w.get_data() == ["abc"]


# ─── Async-загрузка файлов (upload_image: пул потоков через run_in_executor) ──

async def test_queue_file_load_placeholder(qapp, tmp_path):
    """_queue_file_load создаёт placeholder с bytes=None — GUI не блокируется
    (async-конвейер ещё не отработал: задача запланирована, но не выполнена)."""
    img_path = tmp_path / "img.png"
    img_path.write_bytes(_png_bytes(40, 40))

    gw = GalleryWidget()
    gw._queue_file_load(str(img_path))

    # Сразу после вызова в списке один элемент с bytes=None (ещё грузится).
    assert len(gw.items) == 1
    assert gw.items[0]["bytes"] is None
    # get_data пропускает незагруженные элементы.
    assert gw.get_data() == []


async def test_queue_file_load_completes(qapp, tmp_path):
    """После завершения async-конвейера bytes заполняются и get_data их отдаёт."""
    import asyncio
    img_path = tmp_path / "img.png"
    img_path.write_bytes(_png_bytes(40, 40))

    gw = GalleryWidget()
    gw._queue_file_load(str(img_path))

    for _ in range(200):                       # даём конвейеру отработать
        if gw.items and gw.items[0]["bytes"] is not None:
            break
        await asyncio.sleep(0.01)

    assert len(gw.items) == 1
    assert gw.items[0]["bytes"] is not None
    assert len(gw.get_data()) == 1


def test_upload_oversized_stat_check(qapp, tmp_path, monkeypatch):
    """os.stat-проверка отвергает большой файл без чтения (M5-04)."""
    import os as _os
    img_path = tmp_path / "big.png"
    img_path.write_bytes(_png_bytes(10, 10))

    monkeypatch.setattr(_os.path, "getsize", lambda _: 20 * 1024 * 1024)
    monkeypatch.setattr(widgets, "_warn", lambda *a, **k: None)

    gw = GalleryWidget()
    gw.upload_image.__func__   # убеждаемся, что метод существует
    # Вместо диалога подменяем getOpenFileName напрямую
    monkeypatch.setattr(
        widgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(img_path), ""))
    )
    gw.upload_image()
    assert len(gw.items) == 0  # файл отклонён по stat, элемент не добавлен
