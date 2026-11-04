"""Галерея карточки аккаунта: BLOB-картинки, async-конвейер загрузки
(декодирование и сжатие в пуле потоков), миниатюры, лимиты объёма.

Модуль - публичный фасад: чистые шаги конвейера вынесены в gallery_pipeline.py,
просмотр/экспорт - в gallery_viewer.py; все прежние имена реэкспортируются
отсюда (тесты патчат _warn/_read_file/_prepare_image_bytes именно здесь)."""
import os
import asyncio
import logging
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit,
                               QPushButton, QLabel, QFileDialog, QApplication,
                               QMenu)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QPixmap, QImage
import shiboken6
from hranilka.ui.theme import mix
from hranilka.core import util

from hranilka.ui.widgets.common import (FileTooLargeError, _confirm,
                                        _read_file, _warn, make_delete_button,
                                        set_a11y, updates_suspended)
from hranilka.ui.widgets import gallery_viewer
from hranilka.ui.widgets.gallery_pipeline import (
    IMAGE_EXTS as _IMAGE_EXTS,
    MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, MAX_IMAGES_PER_ACCOUNT, MAX_TOTAL_BYTES,
    PRELOAD_CONCURRENCY, ImageBlobCache,
    supported_image_exts as _supported_image_exts,
    downscale_image_bytes as _downscale_image_bytes,
    decode_thumb as _decode_thumb,
    prepare_image_bytes as _prepare_image_bytes,
    decode_image, read_image_size, image_to_png_bytes, guess_ext,
    path_exists_if_image, paths_from_mime)

__all__ = ["GalleryWidget", "FileTooLargeError", "_IMAGE_EXTS",
           "_supported_image_exts", "_downscale_image_bytes", "_decode_thumb",
           "_prepare_image_bytes", "_read_file", "_warn", "_confirm"]


class GalleryWidget(QWidget):
    """Галерея изображений. Элемент хранит байты ТОЛЬКО пока они не сохранены
    в БД (новые/вставленные/из черновика); картинки из БД - миниатюра +
    image_id, исходный BLOB читается по требованию и держится в небольшом
    LRU-кэше (P4)."""

    # Испускается при смене «идут ли сейчас загрузки файлов» (0 задач <-> 1+).
    upload_status_changed = Signal(bool)

    # Лимиты добавляемых изображений (M3-05): защита от исчерпания памяти/диска
    # и «decompression bomb». Значения - из слоя данных, если заданы там.
    _MAX_IMAGE_BYTES = MAX_IMAGE_BYTES
    _MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    _MAX_IMAGES_PER_ACCOUNT = MAX_IMAGES_PER_ACCOUNT
    _MAX_TOTAL_BYTES = MAX_TOTAL_BYTES

    # Совместимость: прежние статические помощники класса.
    _decode_image = staticmethod(decode_image)
    _read_image_size = staticmethod(read_image_size)
    _image_to_png_bytes = staticmethod(image_to_png_bytes)
    _guess_ext = staticmethod(guess_ext)

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.setAcceptDrops(True)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(10)

        btn_layout = QHBoxLayout()
        self.upload_btn = QPushButton("ЗАГРУЗИТЬ С ДИСКА")
        self.upload_btn.clicked.connect(self.upload_image)
        self.paste_btn = QPushButton("ВСТАВИТЬ ИЗ БУФЕРА")
        self.paste_btn.clicked.connect(self.paste_image)
        btn_layout.addWidget(self.upload_btn)
        btn_layout.addWidget(self.paste_btn)
        self.layout.addLayout(btn_layout)

        self.items_layout = QVBoxLayout()
        self.layout.addLayout(self.items_layout)
        self.layout.addStretch()

        # Элемент - dict: bytes (bytes|None - только несохранённые), desc
        # (QLineEdit), del (QPushButton), widget, thumb (QLabel), image_id
        # (int|None), blob_size, thumb_ready (миниатюра отрисована).
        # bytes=None + image_id!=None: BLOB в БД (читается по требованию).
        self.items = []
        self._editable = False
        # LRU-кэш BLOB, прочитанных из БД (ключ - image_id).
        self._blob_cache = ImageBlobCache()
        # Провайдер суммарного объёма галереи прочих аккаунтов (set_size_context).
        self._other_bytes_provider = None
        # Колбэк ленивой загрузки BLOB: loader_fn(image_id) -> bytes | None.
        self._image_loader = None
        # Async-чтение BLOB через координатор БД (run_async с токеном сессии,
        # H65-02): reader(image_id, session) -> awaitable[bytes|None].
        self._async_reader = None
        self._session_provider = None
        # Поколение предпросмотра: гасит устаревшую async-цепочку.
        self._preload_gen = 0
        # Поколение данных карточки (M65-01/M65-02): растёт при set_data и
        # cancel_all_tasks; импорт добавляет элемент только при том же поколении.
        self._data_gen = 0
        # Активные задачи загрузки файлов (M6-01: Save их дожидается).
        self._upload_tasks = set()
        # Контекст аккаунта и «раковина» для осиротевших загрузок (M7-05).
        self._account_provider = None
        self._orphan_handler = None

    def _downscale_enabled(self):
        """Включено ли сжатие больших изображений при импорте (настройка)."""
        return bool(self.config.get("image_downscale", True)) if self.config else True

    # ─── Стили миниатюр из цветов темы (H-10) ────────────────────────────────

    def _theme_colors(self):
        """(text, bg) из настроек; запасные значения - если config не внедрён."""
        if self.config is not None:
            return (self.config.get("text_color", "#FFFFFF"),
                    self.config.get("tree_bg_color", "#333333"))
        return "#FFFFFF", "#333333"

    def _thumb_css(self, fg=None):
        """CSS миниатюры: фон из темы; fg - None (только фон), "full" (текст
        темы) или "dim" (приглушённый placeholder)."""
        text, bg = self._theme_colors()
        css = f"background-color: {bg};"
        if fg == "full":
            css += f" color: {text};"
        elif fg == "dim":
            css += f" color: {mix(text, bg, 0.4)};"
        return css

    @staticmethod
    def _alive(widget):
        """Qt-объект ещё жив (set_data мог вызвать deleteLater)."""
        try:
            return widget is not None and shiboken6.isValid(widget)
        except Exception:                        # noqa: BLE001
            return False

    # ─── Чтение файлов ───────────────────────────────────────────────────────

    def _read_file_bytes(self, path):
        # Открываем один раз и читаем не более лимита (M6-04/L65-02).
        try:
            return _read_file(path, self._MAX_IMAGE_BYTES)
        except FileTooLargeError:
            self._warn_too_large_file()
            return None
        except OSError as e:
            _warn(self.config, self, "Ошибка", f"Не удалось прочитать файл:\n{e}")
            return None

    async def _read_file_bytes_async(self, path):
        """То же, но чтение - в потоке пула (P4): UI не блокируется."""
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None, _read_file, path, self._MAX_IMAGE_BYTES)
        except FileTooLargeError:
            self._warn_too_large_file()
        except OSError as e:
            _warn(self.config, self, "Ошибка", f"Не удалось прочитать файл:\n{e}")
        return None

    def _warn_too_large_file(self):
        mb = self._MAX_IMAGE_BYTES // (1024 * 1024)
        _warn(self.config, self, "Слишком большой файл",
              f"Файл больше {mb} МБ и не будет загружен.")

    def set_size_context(self, other_bytes_provider):
        """Внедрить провайдер суммарного объёма галереи ОСТАЛЬНЫХ аккаунтов в БД
        (лимит общего объёма). None - лимит общего объёма не проверяется."""
        self._other_bytes_provider = other_bytes_provider

    def set_image_loader(self, loader_fn):
        """Установить колбэк ленивой загрузки BLOB из БД.

        gallery_thumb_preload = "startup" (по умолчанию): сразу запускаем
        фоновый предпросмотр миниатюр; "on_click" - BLOB читается только по
        нажатию (M6-03). loader_fn(image_id: int) -> bytes | None."""
        self._image_loader = loader_fn
        if self._preload_on_start_enabled():
            self._start_thumb_preload()

    def _preload_on_start_enabled(self):
        mode = self.config.get("gallery_thumb_preload", "startup") if self.config else "startup"
        return mode != "on_click"

    def set_async_reader(self, reader, session_provider):
        """Внедрить async-чтение BLOB через координатор БД (H65-02)."""
        self._async_reader = reader
        self._session_provider = session_provider

    def set_account_provider(self, provider):
        """Внедрить провайдер id текущего аккаунта (M7-05)."""
        self._account_provider = provider

    def set_orphan_upload_handler(self, handler):
        """handler(account_id, desc_text, data_bytes) - для загрузок, завершившихся
        после смены карточки (M7-05)."""
        self._orphan_handler = handler

    def _current_account_id(self):
        if self._account_provider is None:
            return None
        try:
            return self._account_provider()
        except Exception:                        # noqa: BLE001 - teardown/гонки
            return None

    def _orphan_limit_context(self):
        """Снять лимитный контекст исходной карточки до первого await."""
        provider = self._other_bytes_provider
        other = None
        if provider is not None:
            try:
                other = provider()
            except Exception:                   # проверка БД при save всё равно fail-closed
                other = None
        return other, self._local_bytes(), len(self.items)

    def _emit_orphan(self, account_id, desc_text, data):
        if self._orphan_handler is None or account_id is None:
            return
        try:
            self._orphan_handler(account_id, desc_text, data)
        except Exception as e:                   # noqa: BLE001 - не валим конвейер
            logging.error("Не удалось передать осиротевшую загрузку: %s",
                          e, exc_info=e)

    # ─── Байты элементов (P4) ────────────────────────────────────────────────

    def _item_bytes(self, item):
        """Байты элемента без обращения к БД: несохранённые - из item, прочие -
        из LRU-кэша (None, если вытеснены/не загружены)."""
        data = item["bytes"]
        if data is not None:
            return data
        if item["image_id"] is not None:
            return self._blob_cache.get(item["image_id"])
        return None

    def _local_bytes(self):
        """Суммарный объём картинок в текущей карточке.

        Несохранённые - по len(bytes); из БД - по blob_size (M7-03), а если его
        нет - по закэшированным байтам. Приоритет у фактических байтов."""
        total = 0
        for it in self.items:
            if it["bytes"] is not None:
                total += len(it["bytes"])
                continue
            size = it.get("blob_size")
            if not size and it.get("image_id") is not None:
                cached = self._blob_cache.get(it["image_id"])
                size = len(cached) if cached is not None else 0
            total += size or 0
        return total

    def _accept_image(self, data):
        """Синхронная проверка добавляемого изображения (размер снимается по
        метаданным)."""
        return self._accept_image_checked(data, read_image_size(data))

    def _accept_image_checked(self, data, size, pending_placeholder=False):
        """Проверяет ДОБАВЛЯЕМОЕ изображение при УЖЕ известном размере: размер
        файла, разрешение (защита от «бомбы»), число картинок на аккаунт и
        суммарный объём по базе. Возвращает True, если можно сохранить.

        pending_placeholder - кандидат уже добавлен в self.items как placeholder
        (путь upload): при подсчёте лимита его исключаем (L65-01)."""
        if not data:
            return False
        if len(data) > self._MAX_IMAGE_BYTES:
            mb = self._MAX_IMAGE_BYTES // (1024 * 1024)
            _warn(self.config, self, "Слишком большой файл",
                  f"Изображение больше {mb} МБ и не будет добавлено.")
            return False
        if size is None:
            _warn(self.config, self, "Ошибка",
                  "Файл не распознан как изображение.")
            return False
        if size[0] * size[1] > self._MAX_IMAGE_PIXELS:
            mp = self._MAX_IMAGE_PIXELS // 1_000_000
            _warn(self.config, self, "Слишком большое изображение",
                  f"Разрешение превышает {mp} Мп и не будет добавлено.")
            return False
        committed = len(self.items) - (1 if pending_placeholder else 0)
        if committed >= self._MAX_IMAGES_PER_ACCOUNT:
            _warn(self.config, self, "Слишком много изображений",
                  f"На один аккаунт допускается не более "
                  f"{self._MAX_IMAGES_PER_ACCOUNT} изображений.")
            return False
        provider = getattr(self, "_other_bytes_provider", None)
        other = 0
        if provider is not None:
            try:
                other = provider()
            except Exception as e:
                # L5-01: fail-closed - не зная объёма базы, отклоняем (M-14: без
                # сырого текста исключения для пользователя).
                logging.error("Не удалось проверить общий объём галереи: %s",
                              e, exc_info=e)
                _warn(self.config, self, "Ошибка",
                      "Не удалось проверить общий объём галереи "
                      f"({type(e).__name__}). Подробности - в логе программы.")
                return False
        if other + self._local_bytes() + len(data) > self._MAX_TOTAL_BYTES:
            gb = self._MAX_TOTAL_BYTES / (1024 * 1024 * 1024)
            _warn(self.config, self, "Превышен общий объём",
                  f"Суммарный объём изображений в базе превысит "
                  f"{gb:.1f} ГБ - изображение не будет добавлено.")
            return False
        return True

    def upload_image(self):
        pattern = " ".join("*" + e for e in _IMAGE_EXTS)
        flt = f"Изображения ({pattern});;Все файлы (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Выбрать картинку", "", flt)
        if not path:
            return
        # Размер проверяем по os.stat ДО чтения (M5-04).
        try:
            file_size = os.path.getsize(path)
        except OSError as e:
            _warn(self.config, self, "Ошибка", f"Не удалось прочитать файл:\n{e}")
            return
        if file_size > self._MAX_IMAGE_BYTES:
            self._warn_too_large_file()
            return
        self._queue_file_load(path)

    def _apply_thumb(self, lbl, thumb_img):
        """Поставить готовую миниатюру (QImage) в QLabel - только в UI-потоке
        (QPixmap вне UI-потока недопустим). Возвращает True, если миниатюра есть."""
        if not self._alive(lbl):
            return False
        if thumb_img is not None and not thumb_img.isNull():
            lbl.setStyleSheet(self._thumb_css())
            lbl.setText("")
            lbl.setPixmap(QPixmap.fromImage(thumb_img).scaled(
                100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            return True
        lbl.setStyleSheet(self._thumb_css("full"))
        lbl.setText("[ нет\nпревью ]")
        return False

    def _show_thumb(self, item, thumb_img):
        item["thumb_ready"] = self._apply_thumb(item["thumb"], thumb_img)

    def _track_upload(self, coro):
        """Запустить async-импорт и учесть задачу (Save ждёт, cancel отменяет)."""
        was_empty = not self._upload_tasks
        task = util.fire(coro)
        if asyncio.isfuture(task):
            self._upload_tasks.add(task)
            task.add_done_callback(self._on_upload_task_done)
            if was_empty:
                self.upload_status_changed.emit(True)

    def _on_upload_task_done(self, task):
        self._upload_tasks.discard(task)
        if not self._upload_tasks:
            self.upload_status_changed.emit(False)

    def _queue_file_load(self, path):
        """Сразу добавить placeholder (bytes=None) и запустить конвейер."""
        limit_context = self._orphan_limit_context()
        self.add_item(None)                  # placeholder «[ фото ]», bytes=None
        item = self.items[-1]
        aid = self._current_account_id()
        self._track_upload(
            self._upload_pipeline(item, path, self._data_gen, aid, limit_context))

    def _cancel_upload_tasks(self):
        tasks = list(self._upload_tasks)
        for t in tasks:
            t.cancel()
        self._upload_tasks.clear()
        if tasks:
            self.upload_status_changed.emit(False)
        return len(tasks)

    def cancel_all_tasks(self):
        """Погасить весь фоновый асинхрон виджета перед сменой сессии БД
        (lock/restore/close). Кэш BLOB сбрасываем: id другой сессии могут
        совпасть."""
        self._data_gen += 1
        self._preload_gen += 1
        self._blob_cache.clear()
        return self._cancel_upload_tasks()

    def has_pending_uploads(self):
        return bool(self._upload_tasks)

    async def wait_pending_uploads(self):
        """Дождаться завершения всех активных загрузок (для Save, M6-01)."""
        if self._upload_tasks:
            await asyncio.gather(*list(self._upload_tasks), return_exceptions=True)

    def _orphan_desc(self, item):
        """Текст описания элемента для осиротевшей загрузки (M7-05); виджет мог
        быть уже удалён - тогда пустая строка."""
        edit = item.get("desc")
        if edit is None:
            return ""
        try:
            if not shiboken6.isValid(edit):
                return ""
            return edit.text()
        except Exception:                        # noqa: BLE001
            return ""

    def _accept_orphan(self, data, size, limit_context):
        """Проверки осиротевшей загрузки с контекстом исходной карточки."""
        if not data or len(data) > self._MAX_IMAGE_BYTES:
            return False
        if size is None or size[0] * size[1] > self._MAX_IMAGE_PIXELS:
            return False
        other, local, count = limit_context or (None, 0, 0)
        if count >= self._MAX_IMAGES_PER_ACCOUNT:
            return False
        if other is not None and other + local + len(data) > self._MAX_TOTAL_BYTES:
            return False
        return True

    async def _upload_pipeline(self, item, path, gen, account_id=None,
                               limit_context=None):
        """Async-конвейер загрузки картинки с диска: чтение файла и
        сжатие + декод миниатюры - в потоках пула, проверка лимитов и
        рисование - в UI-потоке. gen - поколение карточки на старте: если оно
        сменилось, результат уходит аккаунту account_id через orphan-handler
        (M65-02/M7-05)."""
        loop = asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(
                None, _read_file, path, self._MAX_IMAGE_BYTES)
        except FileTooLargeError:
            if gen == self._data_gen and item in self.items:
                self._remove_item_silent(item)
            self._warn_too_large_file()
            return
        except OSError as e:
            if gen == self._data_gen and item in self.items:
                self._remove_item_silent(item)
            _warn(self.config, self, "Ошибка", f"Не удалось прочитать файл:\n{e}")
            return
        data, size, thumb = await loop.run_in_executor(
            None, _prepare_image_bytes, data, self._downscale_enabled())
        if item.get("cancelled"):
            return        # пользователь удалил заглушку: загрузка отменена
        if gen != self._data_gen or item not in self.items:
            if self._accept_orphan(data, size, limit_context):
                self._emit_orphan(account_id, self._orphan_desc(item), data)
            return
        if not self._accept_image_checked(data, size, pending_placeholder=True):
            self._remove_item_silent(item)   # предупреждение уже показал accept
            return
        item["bytes"] = data
        self._show_thumb(item, thumb)

    # ─── Предпросмотр миниатюр из БД (P4) ────────────────────────────────────

    def _start_thumb_preload(self):
        """Запустить async-предпросмотр миниатюр ленивых элементов: чтение BLOB
        и декод - в потоках пула, до PRELOAD_CONCURRENCY одновременно. Сам BLOB
        в элементе не хранится (только в LRU-кэше); _preload_gen гасит цепочку
        при смене карточки."""
        if self._image_loader is None:
            return
        self._preload_gen += 1
        gen = self._preload_gen
        session = self._session_provider() if self._session_provider else None
        pending = [it for it in self.items
                   if it["bytes"] is None and it["image_id"] is not None
                   and not it.get("thumb_ready")]
        util.fire(self._preload_pipeline(pending, gen, session))

    async def _read_blob(self, image_id, session):
        """BLOB из БД: через координатор (токен сессии) либо в потоке пула."""
        if self._async_reader is not None:
            return await self._async_reader(image_id, session)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._image_loader, image_id)

    async def _preload_one(self, item, gen, session, sem):
        loop = asyncio.get_running_loop()
        async with sem:
            if gen != self._preload_gen:
                return
            if (item not in self.items or item["bytes"] is not None
                    or item["image_id"] is None or self._image_loader is None
                    or item.get("thumb_ready")):
                return
            data = await self._read_blob(item["image_id"], session)
            if gen != self._preload_gen or item not in self.items:
                return
            if data is None:
                self._show_thumb(item, None)
                return
            thumb = await loop.run_in_executor(None, _decode_thumb, data, 100)
            if gen != self._preload_gen or item not in self.items:
                return
            self._blob_cache.put(item["image_id"], data)   # LRU, не в элемент
            self._show_thumb(item, thumb)

    async def _preload_pipeline(self, items, gen, session):
        """Пул из PRELOAD_CONCURRENCY воркеров, берущих элементы из общей
        очереди (корутины создаются лениво - без «never awaited» при отмене)."""
        queue = list(reversed(items))
        sem = asyncio.Semaphore(PRELOAD_CONCURRENCY)

        async def worker():
            while queue:
                await self._preload_one(queue.pop(), gen, session, sem)

        workers = min(PRELOAD_CONCURRENCY, len(queue))
        results = await asyncio.gather(*(worker() for _ in range(workers)),
                                       return_exceptions=True)
        for res in results:
            if isinstance(res, BaseException) and not isinstance(
                    res, asyncio.CancelledError):
                raise res                # как раньше: ошибка чтения попадает в лог

    def _remove_item_silent(self, item):
        """Убрать элемент галереи без подтверждения (для отклонённой загрузки)."""
        item["cancelled"] = True
        for i, it in enumerate(self.items):
            if it is item:
                self.items.pop(i)
                break
        w = item["widget"]
        w.hide()
        self.items_layout.removeWidget(w)
        w.deleteLater()
        self._chain_tab_order()

    # ─── Вставка из буфера и перетаскивание ──────────────────────────────────

    def paste_image(self):
        """Вставка из буфера: отслеживаемая задача (M65-01), результат проверяет
        поколение карточки и не попадёт в чужой аккаунт."""
        aid = self._current_account_id()
        limit_context = self._orphan_limit_context()
        self._track_upload(self._paste_pipeline(self._data_gen, aid, limit_context))

    async def _ingest_bytes(self, data, gen, account_id, limit_context):
        """Единый хвост paste/drop: сжатие + размер + миниатюра в фоне (M6-02),
        затем лимиты и добавление (или orphan при смене карточки).
        Возвращает True, если картинка добавлена."""
        loop = asyncio.get_running_loop()
        data, size, thumb = await loop.run_in_executor(
            None, _prepare_image_bytes, data, self._downscale_enabled())
        if gen != self._data_gen:
            # desc пустой: у вставки нет placeholder-элемента.
            if self._accept_orphan(data, size, limit_context):
                self._emit_orphan(account_id, "", data)
            return False
        if self._accept_image_checked(data, size):
            self.add_item(data, thumb=thumb)
            return True
        return False

    async def _paste_pipeline(self, gen, account_id=None, limit_context=None):
        loop = asyncio.get_running_loop()
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        data = None

        # 1. Изображение в буфере (браузер, Paint, скриншот)
        if mime_data.hasImage():
            image = clipboard.image()
            if not image.isNull():
                data = await loop.run_in_executor(None, self._image_to_png_bytes, image)
        # 2. Файл картинки (из Проводника) - чтение в потоке
        elif mime_data.hasUrls():
            paths, _remote = paths_from_mime(mime_data)
            if paths:
                data = await self._read_file_bytes_async(paths[0])
        # 3. Текстовый путь к файлу
        elif mime_data.hasText():
            text = mime_data.text().strip()
            if await loop.run_in_executor(None, path_exists_if_image, text):
                data = await self._read_file_bytes_async(text)

        if data:
            await self._ingest_bytes(data, gen, account_id, limit_context)
        else:
            _warn(
                self.config, self, "Буфер обмена",
                "В буфере нет изображения!\nСкопируйте картинку или файл картинки.",
            )

    def _drop_acceptable(self, mime):
        """Принимаем drop только в режиме правки и только картинки/ссылки."""
        if not self._editable:
            return False
        if mime.hasImage():
            return True
        paths, remote = paths_from_mime(mime)
        return bool(paths) or remote

    def dragEnterEvent(self, event):
        if self._drop_acceptable(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._drop_acceptable(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        if not self._drop_acceptable(mime):
            event.ignore()
            return
        # QMimeData недействителен после возврата из обработчика - забираем всё
        # синхронно: пути и (если нет файлов) копию изображения.
        paths, remote = paths_from_mime(mime)
        image = None
        if not paths and mime.hasImage():
            raw = mime.imageData()
            image = raw if isinstance(raw, QImage) else QImage(raw)
        event.acceptProposedAction()
        self.import_dropped(paths, image, remote)

    def import_dropped(self, paths, image=None, remote=False):
        """Импорт перетащенных источников тем же конвейером и с теми же
        лимитами, что и вставка (отслеживаемая задача, orphan-контракт)."""
        if not self._editable:
            return
        if remote and not paths and image is None:
            _warn(self.config, self, "Перетаскивание",
                  "Ссылки из интернета не загружаются (программа работает "
                  "офлайн). Сохраните файл на диск и перетащите его.")
            return
        aid = self._current_account_id()
        limit_context = self._orphan_limit_context()
        self._track_upload(self._drop_pipeline(
            list(paths), image, self._data_gen, aid, limit_context))

    async def _drop_pipeline(self, paths, image, gen, account_id=None,
                             limit_context=None):
        loop = asyncio.get_running_loop()
        for path in paths:
            if gen != self._data_gen:
                break
            data = await self._read_file_bytes_async(path)
            if data:
                await self._ingest_bytes(data, gen, account_id, limit_context)
            if len(self.items) >= self._MAX_IMAGES_PER_ACCOUNT:
                break                    # дальше - одно и то же предупреждение
        if image is not None and not image.isNull():
            data = await loop.run_in_executor(None, self._image_to_png_bytes, image)
            await self._ingest_bytes(data, gen, account_id, limit_context)

    # ─── Элементы ────────────────────────────────────────────────────────────

    def add_item(self, image_bytes, desc="", image_id=None, blob_size=None,
                 thumb=None):
        """Добавить элемент галереи.

        image_bytes - байты в памяти (несохранённая картинка); если None и
        image_id задан - placeholder ленивой загрузки. thumb - уже готовая
        миниатюра (QImage), иначе она декодируется в потоке пула (P4).
        blob_size - размер BLOB ленивого элемента в БД (лимит объёма, M7-03)."""
        item_widget = QWidget(self)
        _text, _bg = self._theme_colors()
        item_widget.setStyleSheet(
            f"border: 1px solid {mix(_text, _bg, 0.5)}; padding: 5px;")
        h_layout = QHBoxLayout(item_widget)

        thumb_label = QLabel()
        thumb_label.setFixedSize(100, 100)
        thumb_label.setAlignment(Qt.AlignCenter)
        thumb_label.setCursor(Qt.PointingHandCursor)
        thumb_label.setFocusPolicy(Qt.StrongFocus)
        set_a11y(thumb_label, "Миниатюра изображения",
                 "Enter или Пробел - открыть; контекстное меню - экспорт")

        thumb_label.setStyleSheet(self._thumb_css("dim"))
        thumb_label.setText("[ … ]" if image_bytes is not None else "[ фото ]")

        h_layout.addWidget(thumb_label)

        v_layout = QVBoxLayout()
        desc_edit = QLineEdit(desc)
        desc_edit.setPlaceholderText("Описание картинки...")
        desc_edit.setAccessibleName("Описание картинки")
        desc_edit.setReadOnly(not self._editable)
        v_layout.addWidget(desc_edit)

        btn_row = QHBoxLayout()
        # Родитель задаём СРАЗУ (иначе setVisible покажет top-level окно).
        del_btn = make_delete_button(item_widget, "Удалить изображение")
        del_btn.setVisible(self._editable)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        v_layout.addLayout(btn_row)

        h_layout.addLayout(v_layout)
        self.items_layout.addWidget(item_widget)

        item = {"bytes": image_bytes, "desc": desc_edit, "del": del_btn,
                "widget": item_widget, "thumb": thumb_label, "image_id": image_id,
                "blob_size": blob_size, "thumb_ready": False,
                "cancelled": False}
        self.items.append(item)

        del_btn.clicked.connect(lambda: self.remove_item(item_widget))

        # ЛКМ / Enter / Пробел - увеличенный просмотр (ленивая загрузка при нужде).
        thumb_label.mousePressEvent = lambda e, it=item: (
            self._on_thumb_click(it) if e.button() == Qt.LeftButton else None)
        thumb_label.keyPressEvent = lambda e, it=item, lbl=thumb_label: (
            self._on_thumb_key(e, it, lbl))
        # ПКМ по миниатюре - экспорт изображения (в файл / в буфер обмена).
        thumb_label.setContextMenuPolicy(Qt.CustomContextMenu)
        thumb_label.customContextMenuRequested.connect(
            lambda pos, it=item, lbl=thumb_label: self._show_image_menu_lazy(lbl, pos, it))
        self._chain_tab_order()

        if image_bytes is not None:
            if thumb is not None:
                self._show_thumb(item, thumb)
            else:
                util.fire(self._decode_thumb_async(item, image_bytes))

    async def _decode_thumb_async(self, item, data):
        """Декод миниатюры несохранённых байт в потоке пула (P4); без активного
        event-loop util.fire выполнит корутину синхронно (тесты)."""
        loop = asyncio.get_running_loop()
        thumb = await loop.run_in_executor(None, _decode_thumb, data, 100)
        if item in self.items:
            self._show_thumb(item, thumb)

    def _chain_tab_order(self):
        """Порядок Tab: кнопки загрузки -> по каждому элементу миниатюра,
        описание, [X]."""
        chain = [self.upload_btn, self.paste_btn]
        for it in self.items:
            chain += [it["thumb"], it["desc"], it["del"]]
        for a, b in zip(chain, chain[1:]):
            QWidget.setTabOrder(a, b)

    def remove_item(self, widget):
        if not _confirm(self.config, self, "Удаление",
                        "Удалить это изображение из галереи?"):
            return
        for i, it in enumerate(self.items):
            if it["widget"] == widget:
                it["cancelled"] = True   # in-flight загрузка не должна воскреснуть
                self.items.pop(i)
                if it["image_id"] is not None:
                    self._blob_cache.discard(it["image_id"])
                break
        widget.hide()
        self.items_layout.removeWidget(widget)
        widget.deleteLater()
        self._chain_tab_order()

    async def _load_lazy_bytes_async(self, item):
        """Получить байты ленивого элемента для просмотра/экспорта: из кэша или
        async-чтением BLOB (H-7, токен сессии снимается один раз). Результат
        кладётся в LRU-кэш, а не в элемент (P4). Возвращает bytes или None."""
        data = self._item_bytes(item)
        if data is not None:
            return data
        if item["image_id"] is None or self._image_loader is None:
            return None
        session = self._session_provider() if self._session_provider else None
        data = await self._read_blob(item["image_id"], session)
        if item not in self.items:
            return None                          # элемент удалили за время чтения
        if data is not None:
            self._blob_cache.put(item["image_id"], data)
            if not item.get("thumb_ready"):
                loop = asyncio.get_running_loop()
                thumb = await loop.run_in_executor(None, _decode_thumb, data, 100)
                if item in self.items:
                    self._show_thumb(item, thumb)
        return data

    def _set_thumb_loading(self, item, loading):
        """Состояние «загрузка» на миниатюре, пока идёт чтение BLOB (H-7)."""
        lbl = item["thumb"]
        if not self._alive(lbl):
            return
        if loading:
            lbl.setStyleSheet(self._thumb_css("dim"))
            lbl.setText("[ … ]")
        elif not item.get("thumb_ready"):
            lbl.setStyleSheet(self._thumb_css("dim"))
            lbl.setText("[ фото ]")

    def _on_thumb_key(self, event, item, lbl):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self._on_thumb_click(item)
        else:
            QLabel.keyPressEvent(lbl, event)

    def _on_thumb_click(self, item):
        """ЛКМ по миниатюре: байты в памяти/кэше - показываем сразу, иначе
        async-загрузка (UI не виснет, H-7)."""
        data = self._item_bytes(item)
        if data is not None:
            self.show_full_image(data)
            return
        if item.get("_loading"):
            return                               # защита от двойного клика
        util.fire(self._on_thumb_click_async(item))

    async def _on_thumb_click_async(self, item):
        item["_loading"] = True
        self._set_thumb_loading(item, True)
        try:
            data = await self._load_lazy_bytes_async(item)
        finally:
            if item in self.items:
                item["_loading"] = False
                self._set_thumb_loading(item, False)
        if data and item in self.items:
            self.show_full_image(data)

    def _show_image_menu_lazy(self, label, pos, item):
        """Контекстное меню миниатюры с ленивой загрузкой перед экспортом."""
        menu = QMenu(self)
        act_file = menu.addAction("Экспорт в файл…")
        act_clip = menu.addAction("Экспорт в буфер обмена")
        chosen = menu.exec(label.mapToGlobal(pos))
        if chosen not in (act_file, act_clip):
            return
        data = self._item_bytes(item)
        if data is not None:
            self._export_menu_action(chosen, act_file, data)
            return
        if item.get("_loading"):
            return
        util.fire(self._show_image_menu_lazy_async(chosen, act_file, item))

    async def _show_image_menu_lazy_async(self, chosen, act_file, item):
        item["_loading"] = True
        self._set_thumb_loading(item, True)
        try:
            data = await self._load_lazy_bytes_async(item)
        finally:
            if item in self.items:
                item["_loading"] = False
                self._set_thumb_loading(item, False)
        if item not in self.items:
            return
        if not data:
            _warn(self.config, self, "Ошибка", "Изображение недоступно.")
            return
        self._export_menu_action(chosen, act_file, data)

    def _export_menu_action(self, chosen, act_file, data):
        if chosen == act_file:
            self._export_image_to_file(data)
        else:
            self._export_image_to_clipboard(data)

    def _warn_cb(self, title, text):
        _warn(self.config, self, title, text)

    def show_full_image(self, image_bytes):
        gallery_viewer.show_full_image(self.config, self, image_bytes)

    def _export_image_to_file(self, image_bytes):
        gallery_viewer.export_image_to_file(self, image_bytes, self._warn_cb)

    def _export_image_to_clipboard(self, image_bytes):
        gallery_viewer.export_image_to_clipboard(image_bytes, self._warn_cb)

    # ─── Контракт данных ─────────────────────────────────────────────────────

    def get_data(self):
        """Список элементов галереи для сохранения в БД (контракт H-6).

        Каждый элемент - {"desc", "data": bytes|None, "image_id": int|None,
        "blob_size"}: несохранённые (bytes есть) идут с data; сохранённые в БД
        (bytes=None, image_id задан) - data=None + image_id: БД оставит
        существующий BLOB. Пустой placeholder (ни bytes, ни image_id)
        пропускается."""
        result = []
        for it in self.items:
            data = it["bytes"]
            image_id = it["image_id"]
            if data is None and image_id is None:
                continue                         # нечего сохранять
            result.append({"desc": it["desc"].text(),
                           "data": data, "image_id": image_id,
                           "blob_size": it.get("blob_size")})
        return result

    def assign_saved_ids(self, saved_ids):
        """Присваивает элементам id строк БД после успешного сохранения.

        saved_ids - результат Database.save_account*: id в порядке и составе
        get_data(). Сохранённые байты больше не держим в элементе (P4): они
        уходят в LRU-кэш, элемент становится «ленивым» (image_id + blob_size) -
        следующий get_data отдаст data=None, БД сохранит BLOB как есть."""
        savable = [it for it in self.items
                   if it["bytes"] is not None or it["image_id"] is not None]
        if len(savable) != len(saved_ids):
            return
        for it, new_id in zip(savable, saved_ids):
            if new_id is None:
                continue
            it["image_id"] = new_id
            if it["bytes"] is not None:
                self._blob_cache.put(new_id, it["bytes"])
                it["blob_size"] = len(it["bytes"])
                it["bytes"] = None

    def set_data(self, data):
        """Загрузить список элементов галереи.

          {"data": bytes, "desc": str}                  - байты в памяти
          {"image_id": int, "desc": str, "data": None}  - из БД (ленивый)
        image_id читается из "id" (ответ load_account) или "image_id" (кэш
        правок, H-6).

        Смена карточки поднимает _data_gen: ещё идущий импорт доживает в фоне
        и уходит своему аккаунту через orphan-handler (M7-05)."""
        self._data_gen += 1
        self._blob_cache.clear()     # id разных карточек/таблиц не смешиваем
        with updates_suspended(self):
            for it in self.items:
                self.items_layout.removeWidget(it["widget"])
                it["widget"].deleteLater()
            self.items.clear()
            for item in data:
                img_id = item.get("id", item.get("image_id"))
                img_data = item.get("data")
                desc = item.get("desc", "")
                blob_size = item.get("blob_size")
                if img_data is not None:
                    self.add_item(img_data, desc, image_id=img_id)
                elif img_id is not None:
                    self.add_item(None, desc, image_id=img_id, blob_size=blob_size)
                # Элементы без data и без id игнорируются.

    def set_editable(self, editable):
        self._editable = editable
        self.upload_btn.setVisible(editable)
        self.paste_btn.setVisible(editable)
        for it in self.items:
            it["desc"].setReadOnly(not editable)
            it["del"].setVisible(editable)
