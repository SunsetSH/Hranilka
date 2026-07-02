"""GUI-тесты исправлений ревью 0.6.5 (галерея).

  * L65-01 — лимит картинок работает без off-by-one (импорт доходит ровно до 50);
  * M65-01/M65-02 — импорт привязан к поколению карточки: завершившийся после
    смены карточки результат не попадает в чужой аккаунт.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage, QImageReader

import widgets
from widgets import GalleryWidget


# Общий session-qapp живёт в conftest.py (M-16). setAllocationLimit —
# процессно-глобальная настройка Qt; вызываем на импорте модуля.
QImageReader.setAllocationLimit(256)


@pytest.fixture(autouse=True)
def _silence_dialogs(monkeypatch):
    monkeypatch.setattr(widgets, "_warn", lambda *a, **k: None)
    monkeypatch.setattr(widgets, "_confirm", lambda *a, **k: True)


def _png_bytes(w=10, h=10):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(0)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


# ─── L65-01: off-by-one лимита картинок ──────────────────────────────────────

def test_upload_reaches_exact_limit(qapp):
    """Placeholder не должен «съедать» последний слот: при лимите N импорт N-й
    картинки принимается, N+1-й — отклоняется (L65-01)."""
    gw = GalleryWidget()
    gw._MAX_IMAGES_PER_ACCOUNT = 2
    data, size = _png_bytes(), (10, 10)

    gw.add_item(data)                # 1 реальная картинка
    gw.add_item(None)                # + placeholder текущей загрузки → items == 2
    # committed == 1 (placeholder исключается) < 2 → 2-я картинка проходит.
    assert gw._accept_image_checked(data, size, pending_placeholder=True) is True

    gw.items[-1]["bytes"] = data     # placeholder «дозагрузился» → 2 реальных
    gw.add_item(None)                # ещё один placeholder → items == 3
    # committed == 2 >= 2 → лимит достигнут, следующая отклоняется.
    assert gw._accept_image_checked(data, size, pending_placeholder=True) is False


def test_paste_count_uses_committed(qapp):
    """paste (без placeholder) считает существующие картинки как есть (default)."""
    gw = GalleryWidget()
    gw._MAX_IMAGES_PER_ACCOUNT = 2
    data, size = _png_bytes(), (10, 10)
    gw.add_item(data)
    gw.add_item(data)                # 2 реальных == лимит
    assert gw._accept_image_checked(data, size) is False


# ─── M65-01/M65-02: привязка импорта к поколению карточки ─────────────────────

def test_set_data_bumps_generation_and_cancels_uploads(qapp):
    gw = GalleryWidget()
    gen0 = gw._data_gen
    gw.set_data([])                  # «переключение карточки»
    assert gw._data_gen != gen0


def test_cancel_all_tasks_advances_generation(qapp):
    gw = GalleryWidget()
    g0 = gw._data_gen
    p0 = gw._preload_gen
    gw.cancel_all_tasks()
    assert gw._data_gen != g0
    assert gw._preload_gen != p0


async def test_upload_pipeline_discards_result_after_card_switch(qapp, monkeypatch):
    """Если карточку сменили, пока грузилась картинка, результат НЕ применяется к
    текущему (уже другому) аккаунту общего виджета (M65-02)."""
    gw = GalleryWidget()
    gw.add_item(None)
    item = gw.items[-1]
    assert item["bytes"] is None

    data = _png_bytes()
    monkeypatch.setattr(widgets, "_read_file", lambda path, mx: data)
    monkeypatch.setattr(widgets, "_prepare_image_bytes",
                        lambda d, downscale: (d, (10, 10), None))

    stale_gen = gw._data_gen
    gw._data_gen += 1                # смена карточки во время «загрузки»

    await gw._upload_pipeline(item, "x.png", stale_gen)
    assert item["bytes"] is None     # результат отброшен, в чужую карточку не попал
