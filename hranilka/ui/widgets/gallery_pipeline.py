"""Чистые (без виджетов) шаги конвейера галереи: форматы, декод/сжатие,
LRU-кэш BLOB, разбор перетаскиваемых/вставляемых источников.

Функции CPU-/IO-bound и не трогают Qt-виджеты - безопасно вызываются из
пула потоков (loop.run_in_executor). QImage можно создавать вне UI-потока,
QPixmap - нет (рисование остаётся в gallery.py)."""
import os
from collections import OrderedDict

from PySide6.QtCore import Qt, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImageReader

from hranilka.data.database import gallery_ops as _gallery_ops

# Лимиты: единый источник - слой данных (если там заданы), иначе локальные.
MAX_IMAGE_BYTES = getattr(_gallery_ops, "GALLERY_MAX_IMAGE_BYTES",
                          15 * 1024 * 1024)            # 15 МБ на файл
MAX_IMAGES_PER_ACCOUNT = getattr(_gallery_ops, "GALLERY_MAX_IMAGES_PER_OWNER",
                                 50)                   # картинок на аккаунт
MAX_IMAGE_PIXELS = 50 * 1_000_000                      # 50 Мп (по метаданным)
MAX_TOTAL_BYTES = _gallery_ops.GALLERY_TOTAL_BYTES_LIMIT

BLOB_CACHE_BYTES = 64 * 1024 * 1024    # P4: общий лимит кэша BLOB из БД
PRELOAD_CONCURRENCY = 4                # P4: одновременных чтений превью


def supported_image_exts():
    """Расширения изображений, которые умеет читать Qt (вкл. webp, tiff и т.п.,
    если установлены плагины). Запасной набор - на случай пустого ответа."""
    exts = {"." + bytes(f).decode("ascii").lower()
            for f in QImageReader.supportedImageFormats()}
    return tuple(sorted(exts)) or (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")


IMAGE_EXTS = supported_image_exts()


def is_image_path(path):
    """Путь похож на файл картинки по расширению."""
    return bool(path) and path.lower().endswith(IMAGE_EXTS)


def downscale_image_bytes(data, max_side, quality):
    """Уменьшить изображение, если его длинная сторона превышает max_side, и
    перекодировать в JPEG с заданным quality (Баг 3 - производительность).

    Возвращает новые байты или None, если уменьшать не нужно (картинка в
    пределах max_side) либо данные не распознаны как изображение. Декодирование
    идёт через QImageReader.setScaledSize - большой файл не разворачивается в
    память целиком, что и снимает фриз/нагрузку CPU при импорте крупных фото."""
    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.ReadOnly)
    reader = QImageReader(buf)
    reader.setAutoTransform(True)
    if not reader.canRead():
        return None
    size = reader.size()
    if not size.isValid():
        return None
    if size.width() <= max_side and size.height() <= max_side:
        return None
    reader.setScaledSize(size.scaled(max_side, max_side, Qt.KeepAspectRatio))
    img = reader.read()
    if img.isNull():
        return None
    out = QByteArray()
    obuf = QBuffer(out)
    obuf.open(QIODevice.WriteOnly)
    if not img.save(obuf, "JPEG", quality):
        obuf.close()
        return None
    obuf.close()
    return bytes(out)


def read_image_size(data):
    """Размер изображения по метаданным БЕЗ полного декодирования.
    Возвращает (width, height) или None, если файл не распознан как картинка."""
    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.ReadOnly)
    reader = QImageReader(buf)
    if not reader.canRead():
        return None
    size = reader.size()
    return (size.width(), size.height()) if size.isValid() else (0, 0)


def decode_image(data, bound=None):
    """Декодировать изображение через QImageReader (учитывает глобальный
    allocation-limit, см. setAllocationLimit при старте). bound - ограничить
    сторону при чтении (для миниатюр, экономит память). Возвращает QImage или
    None при сбое/повреждении/превышении лимита памяти."""
    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.ReadOnly)
    reader = QImageReader(buf)
    reader.setAutoTransform(True)
    if bound is not None and reader.canRead():
        size = reader.size()
        if size.isValid() and (size.width() > bound or size.height() > bound):
            reader.setScaledSize(size.scaled(bound, bound, Qt.KeepAspectRatio))
    img = reader.read()
    return img if not img.isNull() else None


def decode_thumb(data, bound):
    """Декодировать миниатюру (QImage) - пригодно для вызова из рабочего потока."""
    return decode_image(data, bound=bound)


def image_to_png_bytes(qimage):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    qimage.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def guess_ext(data):
    """Расширение по сигнатуре байтов изображения (для имени файла экспорта)."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"\x89PNG":
        return ".png"
    if data[:3] == b"GIF":
        return ".gif"
    if data[:2] == b"BM":
        return ".bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def prepare_image_bytes(data, downscale=True):
    """Подготовить загружаемое изображение В ФОНОВОМ ПОТОКЕ: при downscale=True
    сжать крупное фото (downscale -> JPEG), снять размер по метаданным и
    декодировать миниатюру. Возвращает (data, size|None, thumb_qimage|None).

    downscale управляется настройкой image_downscale и применяется одинаково ко
    всем источникам (диск и буфер обмена) - M6-02."""
    if downscale:
        smaller = downscale_image_bytes(data, 2560, 90)
        if smaller is not None:
            data = smaller
    size = read_image_size(data)
    thumb = decode_thumb(data, 100)
    return data, size, thumb


def path_exists_if_image(text):
    """Для вызова в потоке: text - существующий файл-картинка?"""
    return is_image_path(text) and os.path.exists(text)


def paths_from_mime(mime):
    """Локальные файлы-картинки из QMimeData (drop/буфер). Вызывать в UI-потоке
    СРАЗУ: после возврата из dropEvent QMimeData недействителен. Возвращает
    (paths, has_remote): has_remote - были http(s)-ссылки (офлайн-приложение их
    не скачивает)."""
    paths, remote = [], False
    if mime.hasUrls():
        for url in mime.urls():
            if url.isLocalFile():
                local = url.toLocalFile()
                if is_image_path(local):
                    paths.append(local)
            elif url.scheme().lower() in ("http", "https", "ftp"):
                remote = True
    return paths, remote


class ImageBlobCache:
    """LRU-кэш BLOB по ключу (image_id) с ограничением суммарного объёма (P4).

    Раньше полный BLOB оседал в каждом элементе галереи (до ~750 МБ на
    карточку); теперь элемент хранит миниатюру + image_id, а исходные байты
    для просмотра/экспорта берутся из кэша или перечитываются из БД."""

    def __init__(self, max_bytes=BLOB_CACHE_BYTES):
        self.max_bytes = max_bytes
        self._data = OrderedDict()
        self._total = 0

    def __len__(self):
        return len(self._data)

    @property
    def total_bytes(self):
        return self._total

    def get(self, key):
        data = self._data.get(key)
        if data is not None:
            self._data.move_to_end(key)
        return data

    def put(self, key, data):
        if key is None or data is None or len(data) > self.max_bytes:
            return
        old = self._data.pop(key, None)
        if old is not None:
            self._total -= len(old)
        self._data[key] = data
        self._total += len(data)
        while self._total > self.max_bytes and self._data:
            _k, evicted = self._data.popitem(last=False)
            self._total -= len(evicted)

    def discard(self, key):
        old = self._data.pop(key, None)
        if old is not None:
            self._total -= len(old)

    def clear(self):
        self._data.clear()
        self._total = 0
