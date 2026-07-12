"""Тесты ревью 0.7 (галерея).

  * M7-05 — загрузка картинки переживает смену карточки: результат не теряется, а
    уходит своему аккаунту через orphan-handler (в живой виджет / черновик правок);
  * M7-03 — лимит общего объёма учитывает ленивые BLOB (bytes=None) по blob_size.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage, QImageReader

from hranilka.ui.widgets import gallery as widgets
from hranilka.ui.widgets.gallery import GalleryWidget


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


# ─── M7-05: загрузка переживает смену карточки ───────────────────────────────

async def test_orphan_upload_routed_after_card_switch(qapp, monkeypatch):
    """Импорт стартует для аккаунта 7; пока грузится, карточку сменили. Результат
    не попадает в живой виджет, а уходит orphan-handler'у с исходным id и байтами."""
    gw = GalleryWidget()
    orphans = []
    gw.set_account_provider(lambda: 7)
    gw.set_orphan_upload_handler(
        lambda aid, desc, data: orphans.append((aid, desc, data)))

    data = _png_bytes()
    monkeypatch.setattr(widgets, "_read_file", lambda path, mx: data)
    monkeypatch.setattr(widgets, "_prepare_image_bytes",
                        lambda d, downscale: (d, (10, 10), None))

    gw._queue_file_load("x.png")            # захватит account_id=7 на старте
    item = gw.items[-1]
    # «Переключение карточки»: поднимаем поколение данных (как set_data), но виджет
    # placeholder не разрушаем — чтобы pipeline увидел gen != _data_gen и ушёл в
    # orphan-путь. (set_data тут не зовём: его deleteLater ломает qapp-teardown в
    # session-фикстуре при живом executor-потоке.)
    gw._data_gen += 1
    await gw.wait_pending_uploads()

    # Картинка НЕ применена к элементу текущей карточки (bytes так и None).
    assert item["bytes"] is None
    assert gw.get_data() == []
    # Ушла своему аккаунту с исходным id и теми же байтами.
    assert len(orphans) == 1
    aid, _desc, got = orphans[0]
    assert aid == 7
    assert got == data


async def test_paste_orphan_routed_after_card_switch(qapp, monkeypatch):
    """Вставка из буфера тоже переживает смену карточки (M7-05)."""
    gw = GalleryWidget()
    orphans = []
    gw.set_account_provider(lambda: 3)
    gw.set_orphan_upload_handler(
        lambda aid, desc, data: orphans.append((aid, desc, data)))

    data = _png_bytes()
    # Пропускаем чтение буфера: подсовываем готовые байты в _prepare_image_bytes и
    # заставляем ветку hasImage вернуть картинку.
    class _FakeImage:
        def isNull(self):
            return False
    class _FakeMime:
        def hasImage(self):
            return True
        def hasUrls(self):
            return False
        def hasText(self):
            return False
    class _FakeClipboard:
        def mimeData(self):
            return _FakeMime()
        def image(self):
            return _FakeImage()
    monkeypatch.setattr(widgets.QApplication, "clipboard",
                        staticmethod(lambda: _FakeClipboard()))
    monkeypatch.setattr(gw, "_image_to_png_bytes", staticmethod(lambda img: data))
    monkeypatch.setattr(widgets, "_prepare_image_bytes",
                        lambda d, downscale: (d, (10, 10), None))

    gw.paste_image()                         # захватит account_id=3
    gw._data_gen += 1                        # смена карточки (см. upload-тест выше)
    await gw.wait_pending_uploads()

    assert gw.get_data() == []
    assert len(orphans) == 1 and orphans[0][0] == 3 and orphans[0][2] == data


async def test_cancel_all_tasks_suppresses_orphan(qapp, monkeypatch):
    """cancel_all_tasks (lock/restore/close) отменяет задачи — orphan-handler НЕ
    вызывается: сессия БД сменилась, картинку сохранять некуда.

    Держим задачу «в полёте» через asyncio.Event (без блокировки потока пула,
    чтобы не оставить висящий executor-поток к концу теста), затем отменяем."""
    import asyncio

    gw = GalleryWidget()
    orphans = []
    gw.set_account_provider(lambda: 5)
    gw.set_orphan_upload_handler(
        lambda aid, desc, data: orphans.append(aid))

    gate = asyncio.Event()                    # никогда не выставляем — задача висит

    async def _gated_pipeline(item, path, gen, account_id=None, limit_context=None):
        await gate.wait()                     # ждём вечно, пока не отменят
        gw._emit_orphan(account_id, "", _png_bytes())  # сюда дойти не должны

    monkeypatch.setattr(gw, "_upload_pipeline", _gated_pipeline)

    gw._queue_file_load("x.png")
    assert gw.has_pending_uploads() is True

    cancelled = gw.cancel_all_tasks()        # отменяет задачу (session died)
    assert cancelled == 1
    await gw.wait_pending_uploads()          # снятая задача завершится (CancelledError)

    assert orphans == []                     # handler не вызван — задача отменена


# ─── M7-03: учёт ленивых BLOB в лимите общего объёма ─────────────────────────

def test_local_bytes_counts_lazy_blob(qapp):
    """_local_bytes считает ленивый элемент (bytes=None) по blob_size (M7-03)."""
    gw = GalleryWidget()
    gw.add_item(None, image_id=1, blob_size=200)     # ленивый: 200 байт в БД
    gw.add_item(_png_bytes())                         # загруженный: len(bytes)
    loaded_len = len(gw.items[-1]["bytes"])
    assert gw._local_bytes() == 200 + loaded_len


def test_local_bytes_prefers_loaded_bytes(qapp):
    """После подгрузки байтов приоритет у len(bytes), а не у blob_size (M7-03)."""
    gw = GalleryWidget()
    gw.add_item(None, image_id=1, blob_size=999)
    real = _png_bytes()
    gw.items[-1]["bytes"] = real                      # «дозагрузили» BLOB
    assert gw._local_bytes() == len(real)


def test_total_limit_counts_lazy_blob(qapp):
    """Лимит общего объёма учитывает ленивый BLOB (M7-03): без учёта картинка
    прошла бы, с учётом — отклоняется."""
    gw = GalleryWidget()
    gw._MAX_TOTAL_BYTES = 300
    gw.set_size_context(lambda: 0)                    # прочие аккаунты — 0
    gw.set_data([{"image_id": 1, "desc": "", "data": None, "blob_size": 250}])
    new = _png_bytes()
    # 0 (прочие) + 250 (ленивый) + len(new) > 300 → отклонить.
    assert gw._accept_image_checked(new, (10, 10)) is False
    # А без ленивого элемента (объём 0) — та же картинка проходит.
    gw.set_data([])
    assert gw._accept_image_checked(new, (10, 10)) is True
