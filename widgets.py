import os
import asyncio
import logging
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit,
                               QPushButton, QLabel,
                               QTextEdit, QFileDialog, QDialog, QMessageBox,
                               QApplication, QSpinBox, QMenu)
from PySide6.QtCore import (Signal, Qt, QDate, QDateTime, QTime, QByteArray,
                            QBuffer, QIODevice)
from PySide6.QtGui import QPixmap, QImage, QImageReader
import shiboken6
from theme import themed_info, themed_confirm, mix
import util


def _supported_image_exts():
    """Расширения изображений, которые умеет читать Qt (вкл. webp, tiff и т.п.,
    если установлены плагины). Запасной набор — на случай пустого ответа."""
    exts = {"." + bytes(f).decode("ascii").lower()
            for f in QImageReader.supportedImageFormats()}
    return tuple(sorted(exts)) or (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")


_IMAGE_EXTS = _supported_image_exts()


def _warn(config, parent, title, text):
    """Показать предупреждение в стиле программы; при отсутствии config —
    откат на стандартный QMessageBox."""
    if config is not None:
        themed_info(config, parent, title, text)
    else:
        QMessageBox.warning(parent, title, text)


def _confirm(config, parent, title, text):
    """Запрос подтверждения в стиле программы; при отсутствии config — откат
    на стандартный QMessageBox. Возвращает True, если пользователь согласился."""
    if config is not None:
        return themed_confirm(config, parent, title, text)
    return QMessageBox.question(parent, title, text) == QMessageBox.Yes


def heading_label(text):
    """QLabel-заголовок (поля, секции). Кегль на +2 от базового задаётся темой
    через свойство heading."""
    lbl = QLabel(text)
    lbl.setProperty("heading", "true")
    return lbl

class CopyableField(QWidget):
    copy_signal = Signal()

    # Ретро-маркеры кнопки показа пароля (без emoji)
    _MASK_SHOW = "[*]"   # сейчас скрыто (звёздочки) — нажать, чтобы показать
    _MASK_HIDE = "[A]"   # сейчас видно (буквы) — нажать, чтобы скрыть

    def __init__(self, text="", is_password=False, parent=None):
        super().__init__(parent)
        self.is_password = is_password
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.input = QLineEdit(text)
        self.input.setReadOnly(True)
        if is_password:
            self.input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.input)

        self.reveal_btn = None
        if is_password:
            self.reveal_btn = QPushButton(self._MASK_SHOW)
            self.reveal_btn.setFixedWidth(45)
            self.reveal_btn.setCheckable(True)
            self.reveal_btn.setToolTip("Показать / скрыть пароль")
            self.reveal_btn.clicked.connect(self._toggle_reveal)
            layout.addWidget(self.reveal_btn)

        self.copy_btn = QPushButton("[КОП]")
        self.copy_btn.setFixedWidth(60)
        self.copy_btn.clicked.connect(self.do_copy)
        layout.addWidget(self.copy_btn)

    def _toggle_reveal(self):
        shown = self.reveal_btn.isChecked()
        self.input.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.reveal_btn.setText(self._MASK_HIDE if shown else self._MASK_SHOW)

    def do_copy(self):
        text = self.input.text()
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()

    def set_text(self, text): self.input.setText(text)
    def get_text(self): return self.input.text()
    def set_placeholder(self, text): self.input.setPlaceholderText(text)

    def set_editable(self, editable):
        self.input.setReadOnly(not editable)
        self.copy_btn.setVisible(not editable)
        if self.is_password:
            if editable:
                # При редактировании пароль показываем, кнопку показа прячем
                self.input.setEchoMode(QLineEdit.Normal)
                self.reveal_btn.setVisible(False)
            else:
                # В режиме просмотра — снова маскируем
                self.input.setEchoMode(QLineEdit.Password)
                self.reveal_btn.setChecked(False)
                self.reveal_btn.setText(self._MASK_SHOW)
                self.reveal_btn.setVisible(True)

class CopyableDateField(QWidget):
    """Поле даты с маской ввода дд.мм.гггг (+ чч:мм для is_datetime).

    Обычная строка с маской вместо QDateEdit: без календаря и стрелок.
    Поведение маски Qt — как режим Ins: цифра перезаписывает позицию под
    курсором (курсор можно ставить в любое место), Backspace очищает символ,
    точки/двоеточие фиксированы. Пустое или недописанное значение = «не
    задано» (None) — как и раньше, дата не подменяется сегодняшним числом.
    """
    copy_signal = Signal()

    def __init__(self, is_datetime=False, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self._is_datetime = is_datetime
        self.format = "dd.MM.yyyy HH:mm" if is_datetime else "dd.MM.yyyy"

        self.date_widget = QLineEdit()
        # «0» — необязательная цифра: допускает частично заполненное поле.
        self.date_widget.setInputMask("00.00.0000 00:00" if is_datetime
                                      else "00.00.0000")
        self.date_widget.setToolTip("Формат: дд.мм.гггг чч:мм" if is_datetime
                                    else "Формат: дд.мм.гггг")
        self.date_widget.setReadOnly(True)
        layout.addWidget(self.date_widget)

        self.copy_btn = QPushButton("[КОП]")
        self.copy_btn.setFixedWidth(60)
        self.copy_btn.clicked.connect(self.do_copy)
        layout.addWidget(self.copy_btn)

    def do_copy(self):
        dt = self.get_date()
        if dt is None:
            return
        QApplication.clipboard().setText(dt.toString(self.format))
        self.copy_signal.emit()

    def set_date(self, date):
        """Принимает QDate, QDateTime, None или строку. None/пусто → «не задано»."""
        if isinstance(date, QDateTime):
            self.date_widget.setText(date.toString(self.format))
        elif isinstance(date, QDate):
            if self._is_datetime:
                self.date_widget.setText(
                    QDateTime(date, QTime(0, 0)).toString(self.format))
            else:
                self.date_widget.setText(date.toString(self.format))
        else:
            # None или неизвестный формат — «не задано», а не «сегодня».
            self.date_widget.setText("")

    def get_date(self):
        """QDateTime или None, если дата не задана/не дописана.

        Для is_datetime незаполненное время считается 00:00 — дата без
        времени не должна пропадать при сохранении."""
        raw = self.date_widget.text()          # маска: пусто = "..[ :]"
        date_part = raw[:10].strip(" .")
        if not date_part:
            return None
        d = QDate.fromString(raw[:10], "dd.MM.yyyy")
        if not d.isValid():
            return None
        t = QTime(0, 0)
        if self._is_datetime:
            parsed = QTime.fromString(raw[11:16], "HH:mm")
            if parsed.isValid():
                t = parsed
        return QDateTime(d, t)

    def set_editable(self, editable):
        self.date_widget.setReadOnly(not editable)
        self.copy_btn.setVisible(not editable)
        
class CopyableTextEdit(QWidget):
    copy_signal = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        layout.addWidget(self.text_edit)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.copy_btn = QPushButton("[КОП]")
        self.copy_btn.setFixedWidth(60)
        self.copy_btn.clicked.connect(self.do_copy)
        btn_layout.addWidget(self.copy_btn)
        layout.addLayout(btn_layout)
        
    def do_copy(self):
        text = self.text_edit.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()
            
    def set_text(self, text): self.text_edit.setPlainText(text)
    def get_text(self): return self.text_edit.toPlainText()
    def set_editable(self, editable):
        self.text_edit.setReadOnly(not editable)
        self.copy_btn.setVisible(not editable)

class SecretQuestionsWidget(QWidget):
    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(10)
        self.rows = []
        self._editable = False

        self.add_btn = QPushButton("+ ДОБАВИТЬ ВОПРОС")
        self.add_btn.clicked.connect(lambda: self.add_row())
        self.layout.addWidget(self.add_btn)
        self.empty_label = QLabel("Секретных вопросов нет")
        self.layout.addWidget(self.empty_label)
        self.layout.addStretch()
        self._update_empty()

    def _update_empty(self):
        self.empty_label.setVisible(not self.rows)

    def add_row(self, q="", a=""):
        if len(self.rows) >= 5:
            _warn(self.config, self, "Лимит", "Максимум 5 вопросов!")
            return
            
        row_widget = QWidget(self)
        h_layout = QHBoxLayout(row_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(5)

        q_edit = QLineEdit(str(q))
        q_edit.setPlaceholderText("Секретный вопрос...")
        q_edit.setReadOnly(not self._editable)
        a_edit = QLineEdit(str(a))
        a_edit.setPlaceholderText("Ответ...")
        a_edit.setReadOnly(not self._editable)

        del_btn = QPushButton("[X]", row_widget)   # родитель сразу — см. add_item
        del_btn.setFixedWidth(40)
        del_btn.setVisible(self._editable)
        del_btn.clicked.connect(lambda: self.remove_row(row_widget))
        
        h_layout.addWidget(q_edit)
        h_layout.addWidget(a_edit)
        h_layout.addWidget(del_btn)

        self.layout.insertWidget(self.layout.count() - 2, row_widget)
        # Храним прямые ссылки на виджеты строки (L-9): без хрупких поисков
        # через parent()/layout().itemAt(...).
        self.rows.append((q_edit, a_edit, del_btn, row_widget))
        self._update_empty()

    def remove_row(self, row_widget):
        if not _confirm(self.config, self, "Удаление",
                        "Удалить этот секретный вопрос?"):
            return
        for i, (_q, _a, _btn, w) in enumerate(self.rows):
            if w == row_widget:
                self.rows.pop(i)
                break
        self.layout.removeWidget(row_widget)
        row_widget.deleteLater()
        self._update_empty()

    def get_data(self):
        # Не сохраняем строку, если оба связанных поля пустые.
        return [{"q": q.text(), "a": a.text()} for q, a, _btn, _w in self.rows
                if q.text().strip() or a.text().strip()]

    def set_data(self, data):
        for _q, _a, _btn, row_widget in self.rows:
            self.layout.removeWidget(row_widget)
            row_widget.deleteLater()
        self.rows.clear()
        for item in data: self.add_row(item.get("q", ""), item.get("a", ""))
        self._update_empty()
    def set_editable(self, editable):
        self._editable = editable
        self.add_btn.setVisible(editable)
        for q, a, del_btn, _w in self.rows:
            q.setReadOnly(not editable)
            a.setReadOnly(not editable)
            del_btn.setVisible(editable)

class CodeListWidget(QWidget):
    copy_signal = Signal()

    # Лимиты импорта из файла: список кодов заведомо мал, всё сверх — защита
    # от случайно выбранного «не того» файла (OOM / тысячи виджетов).
    _MAX_IMPORT_BYTES = 1024 * 1024
    _MAX_IMPORT_CODES = 100

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(5)
        self.rows = []
        self._editable = False

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(5)
        self.add_btn = QPushButton("+ ДОБАВИТЬ КОД")
        self.add_btn.clicked.connect(lambda: self.add_code())
        btn_layout.addWidget(self.add_btn)
        self.import_btn = QPushButton("ИМПОРТ")
        self.import_btn.clicked.connect(self.import_codes)
        btn_layout.addWidget(self.import_btn)
        self.layout.addLayout(btn_layout)

        self.import_hint = QLabel(
            "Импорт: текстовый документ (.txt), в котором коды разделены "
            "новой строкой (абзацем) — один код станет одним полем.")
        self.import_hint.setWordWrap(True)
        self.layout.addWidget(self.import_hint)

        self.empty_label = QLabel("Резервных кодов нет")
        self.layout.addWidget(self.empty_label)
        self.layout.addStretch()
        self._update_empty()

    def _update_empty(self):
        self.empty_label.setVisible(not self.rows)

    def add_code(self, code_text=""):
        row_widget = QWidget(self)
        h_layout = QHBoxLayout(row_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(5)

        code_edit = QLineEdit(code_text)
        code_edit.setReadOnly(not self._editable)
        code_edit.setPlaceholderText("Код / резервный ключ...")
        h_layout.addWidget(code_edit)
        
        copy_btn = QPushButton("[КОП]", row_widget)  # родитель сразу — см. add_item
        copy_btn.setFixedWidth(60)
        copy_btn.clicked.connect(lambda: self.copy_code(code_edit.text()))
        h_layout.addWidget(copy_btn)

        del_btn = QPushButton("[X]", row_widget)     # родитель сразу — см. add_item
        del_btn.setFixedWidth(40)
        del_btn.clicked.connect(lambda: self.remove_code(row_widget))
        h_layout.addWidget(del_btn)
        
        self.layout.insertWidget(self.layout.count() - 2, row_widget)
        # Прямая ссылка на кнопку удаления (L-9) — без layout().itemAt(...).
        self.rows.append((code_edit, del_btn, row_widget))
        self._update_empty()

    def import_codes(self):
        """Импорт кодов из текстового файла: одна непустая строка — одно поле."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт резервных кодов", "",
            "Текстовые файлы (*.txt);;Все файлы (*)")
        if not path:
            return
        try:
            data = _read_file(path, self._MAX_IMPORT_BYTES)
        except FileTooLargeError:
            _warn(self.config, self, "Слишком большой файл",
                  f"Файл больше {self._MAX_IMPORT_BYTES // (1024 * 1024)} МБ — "
                  f"это не похоже на список кодов.")
            return
        except OSError as e:
            logging.warning("Импорт кодов: не удалось прочитать %s: %s", path, e)
            _warn(self.config, self, "Ошибка",
                  f"Не удалось прочитать файл:\n{e}")
            return
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Файл из старого блокнота/Windows — пробуем системную кириллицу.
            text = data.decode("cp1251", errors="replace")
        codes = [line.strip() for line in text.splitlines() if line.strip()]
        if not codes:
            _warn(self.config, self, "Импорт",
                  "В файле не найдено ни одного кода.")
            return
        if len(codes) > self._MAX_IMPORT_CODES:
            _warn(self.config, self, "Импорт",
                  f"В файле {len(codes)} строк — больше лимита "
                  f"{self._MAX_IMPORT_CODES}. Импорт отменён.")
            return
        for code in codes:
            self.add_code(code)

    def copy_code(self, text):
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()

    def remove_code(self, row_widget):
        if not _confirm(self.config, self, "Удаление",
                        "Удалить этот код / резервный ключ?"):
            return
        for i, (_edit, _btn, widget) in enumerate(self.rows):
            if widget == row_widget:
                self.rows.pop(i)
                break
        self.layout.removeWidget(row_widget)
        row_widget.deleteLater()
        self._update_empty()

    def get_data(self):
        # Не сохраняем пустые коды.
        return [edit.text() for edit, _btn, _w in self.rows if edit.text().strip()]

    def set_data(self, data):
        for _edit, _btn, widget in self.rows:
            self.layout.removeWidget(widget)
            widget.deleteLater()
        self.rows.clear()
        for code in data: self.add_code(code)
        self._update_empty()

    def set_editable(self, editable):
        self._editable = editable
        self.add_btn.setVisible(editable)
        self.import_btn.setVisible(editable)
        self.import_hint.setVisible(editable)
        for code_edit, del_btn, _w in self.rows:
            code_edit.setReadOnly(not editable)
            del_btn.setVisible(editable)

def _downscale_image_bytes(data, max_side, quality):
    """Уменьшить изображение, если его длинная сторона превышает max_side, и
    перекодировать в JPEG с заданным quality (Баг 3 — производительность).

    Возвращает новые байты или None, если уменьшать не нужно (картинка в
    пределах max_side) либо данные не распознаны как изображение. Декодирование
    идёт через QImageReader.setScaledSize — большой файл не разворачивается в
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


def _decode_thumb(data, bound):
    """Декодировать миниатюру (QImage) — пригодно для вызова из рабочего потока.

    QImage можно безопасно создавать вне UI-потока (в отличие от QPixmap), поэтому
    самую тяжёлую часть — декодирование/масштабирование — выносим в пул потоков, а
    в UI-поток отдаём уже готовый QImage."""
    return GalleryWidget._decode_image(data, bound=bound)


# ─── Шаги конвейера загрузки картинок (выполняются в пуле потоков) ───────────
# Эти функции CPU-/IO-bound и НЕ трогают Qt-виджеты, поэтому безопасно гоняются
# в фоновых потоках через loop.run_in_executor. «Разные потоки — разные операции»:
# чтение файла/BLOB и тяжёлый декод/сжатие идут вне UI-потока, а рисование
# (QPixmap) остаётся на UI-потоке (см. _apply_thumb).

class FileTooLargeError(Exception):
    """Файл превысил допустимый размер при ограниченном чтении (L65-02)."""


def _read_file(path, max_bytes):
    """Прочитать файл целиком, но не более max_bytes. Для вызова в фоновом потоке.

    Файл открывается ОДИН раз, а размер проверяется через fstat уже открытого
    дескриптора — без TOCTOU-разрыва между getsize и open. Дополнительно читаем
    не более max_bytes+1 байт: если файл/симлинк подменили на больший уже после
    fstat, лишний байт вскроет это и мы не затянем в память гигабайты (L65-02)."""
    with open(path, "rb") as f:
        if os.fstat(f.fileno()).st_size > max_bytes:
            raise FileTooLargeError(path)
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise FileTooLargeError(path)
    return data


def _prepare_image_bytes(data, downscale=True):
    """Подготовить загружаемое изображение В ФОНОВОМ ПОТОКЕ: при downscale=True
    сжать крупное фото (downscale → JPEG), снять размер по метаданным и
    декодировать миниатюру. Возвращает (data, size|None, thumb_qimage|None).

    downscale управляется настройкой image_downscale и применяется одинаково ко
    всем источникам (диск и буфер обмена) — M6-02."""
    if downscale:
        smaller = _downscale_image_bytes(data, 2560, 90)
        if smaller is not None:
            data = smaller
    size = GalleryWidget._read_image_size(data)
    thumb = _decode_thumb(data, 100)
    return data, size, thumb


class GalleryWidget(QWidget):
    """Галерея изображений. Хранит сами байты картинок (для записи в BLOB),
    а не пути к файлам — чтобы документ был самодостаточным."""

    # Испускается при смене «идут ли сейчас загрузки файлов» (0 задач <-> 1+).
    # UI (статус-бар) подписывается один раз при старте — сам виджет живёт всё
    # время работы карточек, пересоздаётся не он, а его данные (set_data).
    upload_status_changed = Signal(bool)

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.config = config
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

        # Каждый элемент — dict: bytes (bytes|None), desc (QLineEdit),
        # del (QPushButton), widget (QWidget), image_id (int|None).
        # image_id — первичный ключ записи в gallery (для ленивой загрузки);
        # bytes=None + image_id!=None означает: BLOB ещё не загружен из БД.
        self.items = []
        self._editable = False
        # Провайдер суммарного объёма галереи прочих аккаунтов (см.
        # set_size_context); None — лимит общего объёма не проверяется.
        self._other_bytes_provider = None
        # Колбэк ленивой загрузки BLOB: loader_fn(image_id) -> bytes | None.
        # Устанавливается через set_image_loader() после set_data().
        self._image_loader = None
        # Async-чтение BLOB через координатор БД (run_async с фиксированным токеном
        # сессии) — чтобы фоновый предпросмотр не обходил проверку сессии и не читал
        # данные другой сессии vault после lock/restore (H65-02).
        # reader(image_id, session) -> awaitable[bytes|None]; session_provider() -> токен.
        self._async_reader = None
        self._session_provider = None
        # Поколение предпросмотра: гасит устаревшую async-цепочку при смене карточки.
        self._preload_gen = 0
        # Поколение данных карточки: растёт при каждом set_data (загрузка другой
        # карточки) и при cancel_all_tasks (lock/restore/close). Async-импорт
        # (upload/paste) снимает его при старте и добавляет элемент ТОЛЬКО если
        # поколение не сменилось — иначе завершившаяся вставка попала бы в чужую
        # карточку общего виджета (M65-01/M65-02).
        self._data_gen = 0
        # Активные задачи загрузки файлов в галерею. Пока они не завершены, BLOB
        # ещё не в элементе (bytes=None) и get_data его пропустит — сохранение до
        # их завершения потеряло бы картинку (M6-01). Save их дожидается.
        self._upload_tasks = set()
        # Контекст аккаунта и «раковина» для осиротевших загрузок (M7-05). Импорт
        # больше не отменяется при смене карточки — он доживает в фоне, а результат
        # уходит не в чужую активную карточку общего виджета, а обратно тому
        # аккаунту, для которого стартовал (в его черновик правок / живую карточку).
        #   _account_provider() -> id аккаунта на момент СТАРТА загрузки (снимается
        #     до первого await в _queue_file_load/_paste_pipeline);
        #   _orphan_handler(account_id, desc_text, data_bytes) — куда отдать байты,
        #     если карточку сменили за время загрузки.
        self._account_provider = None
        self._orphan_handler = None

    def _downscale_enabled(self):
        """Включено ли сжатие больших изображений при импорте (настройка)."""
        return bool(self.config.get("image_downscale", True)) if self.config else True

    # ─── Стили миниатюр из цветов темы (H-10) ────────────────────────────────
    # Раньше фон/текст миниатюр были жёстко зашиты (#333/#FFC400/#AAAAAA) и на
    # темах вроде Windows 95 (чёрный на серебре) выглядели чужеродно, а текст
    # мог терять контраст. Теперь фон — tree_bg_color, текст — text_color,
    # «приглушённый» вариант — смесь текста с фоном (theme.mix).

    def _theme_colors(self):
        """(text, bg) из настроек; запасные значения — если config не внедрён."""
        if self.config is not None:
            return (self.config.get("text_color", "#FFFFFF"),
                    self.config.get("tree_bg_color", "#333333"))
        return "#FFFFFF", "#333333"

    def _thumb_css(self, fg=None):
        """CSS миниатюры: фон из темы; fg — None (только фон), "full" (текст
        темы, для заметных сообщений) или "dim" (приглушённый placeholder)."""
        text, bg = self._theme_colors()
        css = f"background-color: {bg};"
        if fg == "full":
            css += f" color: {text};"
        elif fg == "dim":
            css += f" color: {mix(text, bg, 0.4)};"
        return css

    def _read_file_bytes(self, path):
        # Открываем один раз и читаем не более лимита (M6-04/L65-02): без TOCTOU
        # между проверкой размера и чтением, без затягивания гигабайтов в память.
        try:
            return _read_file(path, self._MAX_IMAGE_BYTES)
        except FileTooLargeError:
            mb = self._MAX_IMAGE_BYTES // (1024 * 1024)
            _warn(self.config, self, "Слишком большой файл",
                  f"Файл больше {mb} МБ и не будет загружен.")
            return None
        except OSError as e:
            _warn(self.config, self, "Ошибка", f"Не удалось прочитать файл:\n{e}")
            return None

    @staticmethod
    def _image_to_png_bytes(qimage):
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        qimage.save(buf, "PNG")
        buf.close()
        return bytes(ba)

    # Лимиты для добавляемых изображений (M3-05): защищают от исчерпания
    # памяти/диска и «decompression bomb» (картинка с огромным разрешением).
    _MAX_IMAGE_BYTES = 15 * 1024 * 1024         # 15 МБ на файл
    _MAX_IMAGE_PIXELS = 50 * 1_000_000          # 50 Мп (по метаданным, до декодирования)
    _MAX_IMAGES_PER_ACCOUNT = 50                # картинок на аккаунт
    _MAX_TOTAL_BYTES = 500 * 1024 * 1024        # суммарно по всей базе

    def set_size_context(self, other_bytes_provider):
        """Внедрить провайдер суммарного объёма галереи ОСТАЛЬНЫХ аккаунтов в БД
        (для лимита общего объёма). None — лимит общего объёма не проверяется
        (например, когда виджет используется вне главного окна)."""
        self._other_bytes_provider = other_bytes_provider

    def set_image_loader(self, loader_fn):
        """Установить колбэк ленивой загрузки BLOB из БД.

        Если настройка gallery_thumb_preload = "startup" (по умолчанию), сразу
        запускаем фоновый предпросмотр: миниатюры ленивых элементов
        подгружаются автоматически (последовательно, не блокируя UI). В режиме
        "on_click" предзагрузку не делаем — BLOB читается только при нажатии на
        изображение, что снижает пиковое потребление ОЗУ (M6-03).
        loader_fn(image_id: int) -> bytes | None."""
        self._image_loader = loader_fn
        if self._preload_on_start_enabled():
            self._start_thumb_preload()

    def _preload_on_start_enabled(self):
        """Подгружать ли все миниатюры сразу при открытии карточки (настройка)."""
        mode = self.config.get("gallery_thumb_preload", "startup") if self.config else "startup"
        return mode != "on_click"

    def set_async_reader(self, reader, session_provider):
        """Внедрить async-чтение BLOB через координатор БД (H65-02).

        reader(image_id, session) -> awaitable[bytes|None] — читает под run_async
        с зафиксированным токеном сессии; session_provider() -> текущий токен.
        Предпросмотр снимает токен один раз на запуск, поэтому смена сессии
        (lock/restore) прерывает чтение, а не читает чужую БД."""
        self._async_reader = reader
        self._session_provider = session_provider

    def set_account_provider(self, provider):
        """Внедрить провайдер id текущего аккаунта (M7-05). Снимается в момент
        СТАРТА загрузки картинки; если к завершению карточку сменили, результат
        адресуется этому id через orphan-handler, а не текущей карточке."""
        self._account_provider = provider

    def set_orphan_upload_handler(self, handler):
        """Внедрить обработчик осиротевшей загрузки (M7-05): вызывается как
        handler(account_id, desc_text, data_bytes), когда конвейер завершился, а
        карточка уже сменилась (gen != self._data_gen или элемент удалён). Handler
        дописывает картинку в черновик правок / живую карточку своего аккаунта."""
        self._orphan_handler = handler

    def _current_account_id(self):
        """id аккаунта на момент вызова (для захвата при старте загрузки)."""
        if self._account_provider is None:
            return None
        try:
            return self._account_provider()
        except Exception:                        # noqa: BLE001 — teardown/гонки
            return None

    def _emit_orphan(self, account_id, desc_text, data):
        """Отдать осиротевшую загрузку обработчику. Если обработчик не внедрён или
        аккаунт неизвестен — молча роняем (как и раньше при смене карточки)."""
        if self._orphan_handler is None or account_id is None:
            return
        try:
            self._orphan_handler(account_id, desc_text, data)
        except Exception as e:                   # noqa: BLE001 — не валим конвейер
            logging.error("Не удалось передать осиротевшую загрузку: %s",
                          e, exc_info=e)

    def _local_bytes(self):
        """Суммарный объём картинок в текущей (редактируемой) карточке.

        Загруженные элементы считаем по фактическим байтам (len(bytes)); ленивые
        (bytes=None) — по blob_size из БД (M7-03), иначе в режиме on_click ещё не
        подгруженные BLOB не учитывались бы и лимит общего объёма можно было бы
        незаметно превысить. Приоритет — у фактических байтов (после подгрузки
        len(bytes) точнее, а blob_size может отличаться от размера в памяти)."""
        total = 0
        for it in self.items:
            if it["bytes"] is not None:
                total += len(it["bytes"])
            else:
                total += it.get("blob_size") or 0
        return total

    @staticmethod
    def _read_image_size(data):
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

    @staticmethod
    def _decode_image(data, bound=None):
        """Декодировать изображение через QImageReader (учитывает глобальный
        allocation-limit, см. setAllocationLimit при старте). bound — ограничить
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

    def _accept_image(self, data):
        """Синхронная проверка добавляемого изображения (для paste и пр.): сама
        снимает размер по метаданным. В async-загрузке размер уже снят в фоновом
        потоке — там вызывается _accept_image_checked, чтобы не декодировать на UI."""
        return self._accept_image_checked(data, self._read_image_size(data))

    def _accept_image_checked(self, data, size, pending_placeholder=False):
        """Проверяет ДОБАВЛЯЕМОЕ изображение при УЖЕ известном размере: размер
        файла, разрешение (защита от «бомбы»), число картинок на аккаунт и
        суммарный объём по базе. Возвращает True, если можно сохранить. К уже
        сохранённым в БД изображениям не применяется (см. add_item).

        pending_placeholder — кандидат уже добавлен в self.items как placeholder
        (путь upload). Тогда при подсчёте лимита его нужно исключить, иначе при 49
        реальных картинках placeholder делает счётчик 50 и отклоняет сам себя
        (off-by-one, L65-01)."""
        if not data:
            return False
        if len(data) > self._MAX_IMAGE_BYTES:
            mb = self._MAX_IMAGE_BYTES // (1024 * 1024)
            _warn(self.config, self, "Слишком большой файл",
                  f"Изображение больше {mb} МБ и не будет добавлено.")
            return False
        # Разрешение проверяем по метаданным (не разворачивая «бомбу» в память).
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
                # L5-01: раньше ошибка провайдера глоталась с other=0 — лимит
                # суммарного объёма молча отключался. Fail-closed: не зная
                # реального объёма базы, отклоняем добавление.
                # Пользователю — без сырого текста исключения (M-14): там могут
                # быть внутренности БД; детали — в лог.
                logging.error("Не удалось проверить общий объём галереи: %s",
                              e, exc_info=e)
                _warn(self.config, self, "Ошибка",
                      "Не удалось проверить общий объём галереи "
                      f"({type(e).__name__}). Подробности — в логе программы.")
                return False
        if other + self._local_bytes() + len(data) > self._MAX_TOTAL_BYTES:
            gb = self._MAX_TOTAL_BYTES / (1024 * 1024 * 1024)
            _warn(self.config, self, "Превышен общий объём",
                  f"Суммарный объём изображений в базе превысит "
                  f"{gb:.1f} ГБ — изображение не будет добавлено.")
            return False
        return True

    def upload_image(self):
        pattern = " ".join("*" + e for e in _IMAGE_EXTS)
        flt = f"Изображения ({pattern});;Все файлы (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Выбрать картинку", "", flt)
        if not path:
            return
        # Размер проверяем по os.stat ДО чтения (M5-04): файл на гигабайты не
        # читается в память целиком и не вешает/не роняет UI (OOM).
        try:
            file_size = os.path.getsize(path)
        except OSError as e:
            _warn(self.config, self, "Ошибка", f"Не удалось прочитать файл:\n{e}")
            return
        if file_size > self._MAX_IMAGE_BYTES:
            mb = self._MAX_IMAGE_BYTES // (1024 * 1024)
            _warn(self.config, self, "Слишком большой файл",
                  f"Файл больше {mb} МБ и не будет загружен.")
            return
        # Чтение файла — в фоновом потоке: крупное фото не подвешивает UI.
        self._queue_file_load(path)

    def _apply_thumb(self, lbl, thumb_img):
        """Поставить готовую миниатюру (QImage) в QLabel — только в UI-потоке
        (здесь создаётся QPixmap, что вне UI-потока недопустимо)."""
        if thumb_img is not None and not thumb_img.isNull():
            lbl.setStyleSheet(self._thumb_css())
            lbl.setText("")
            lbl.setPixmap(QPixmap.fromImage(thumb_img).scaled(
                100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            lbl.setStyleSheet(self._thumb_css("full"))
            lbl.setText("[ нет\nпревью ]")

    def _track_upload(self, coro):
        """Запустить async-импорт и, под работающим event-loop, учесть его задачу,
        чтобы Save мог дождаться (M6-01), а cancel_all_tasks — отменить."""
        was_empty = not self._upload_tasks
        task = util.fire(coro)
        # Под работающим event-loop fire возвращает Task. Без loop (тесты)
        # конвейер уже отработал синхронно.
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
        """Сразу добавить placeholder (bytes=None — UI не блокируется) и запустить
        async-конвейер загрузки. Реальная работа — в _upload_pipeline."""
        self.add_item(None)                  # placeholder «[ фото ]», bytes=None
        item = self.items[-1]
        # Захватываем аккаунт и поколение ДО первого await (M7-05): если карточку
        # сменят за время загрузки, результат уйдёт этому аккаунту, а не текущему.
        aid = self._current_account_id()
        self._track_upload(
            self._upload_pipeline(item, path, self._data_gen, aid))

    def _cancel_upload_tasks(self):
        """Отменить все активные задачи импорта (смена карточки/lock/restore).
        Возвращает число отменённых задач."""
        tasks = list(self._upload_tasks)
        for t in tasks:
            t.cancel()
        self._upload_tasks.clear()
        if tasks:
            # Сбрасываем статус сразу — не дожидаясь done-callback отменённых
            # задач (может не успеть отработать до пересборки карточки).
            self.upload_status_changed.emit(False)
        return len(tasks)

    def cancel_all_tasks(self):
        """Погасить весь фоновый асинхрон виджета (импорт + предпросмотр) перед
        сменой сессии БД (lock/restore/close). Элементы карточки не трогаем —
        UI пересоберётся при следующем set_data."""
        self._data_gen += 1
        self._preload_gen += 1
        return self._cancel_upload_tasks()

    def has_pending_uploads(self):
        """Идут ли ещё загрузки файлов в галерею (BLOB не готовы для get_data)."""
        return bool(self._upload_tasks)

    async def wait_pending_uploads(self):
        """Дождаться завершения всех активных загрузок файлов (для Save, M6-01).

        После M7-05 загрузки переживают смену карточки, поэтому здесь могут
        оказаться и задачи ДРУГИХ аккаунтов — это допустимо: их результат уйдёт
        своему аккаунту через orphan-handler, а Save дождётся согласованного
        снимка галереи текущей карточки."""
        if self._upload_tasks:
            await asyncio.gather(*list(self._upload_tasks), return_exceptions=True)

    def _orphan_desc(self, item):
        """Текст описания элемента для осиротевшей загрузки (M7-05). Placeholder к
        моменту завершения обычно имеет пустое описание, но если пользователь успел
        что-то ввести — сохраняем его.

        Виджет описания к этому моменту уже мог быть удалён (set_data при смене
        карточки зовёт deleteLater): обращение к освобождённому C++-объекту через
        shiboken бросает RuntimeError. Проверяем валидность shiboken.isValid перед
        доступом и на всякий случай ловим исключение — иначе осиротевшая загрузка
        падала бы вместо мягкого «пустого описания»."""
        edit = item.get("desc")
        if edit is None:
            return ""
        try:
            if not shiboken6.isValid(edit):
                return ""
            return edit.text()
        except Exception:                        # noqa: BLE001 — виджет мог исчезнуть
            return ""

    def _accept_orphan(self, data, size):
        """Проверки осиротевшей загрузки, НЕ зависящие от аккаунта (M7-05): размер
        файла и разрешение («бомба»). Пер-элементные проверки уже сделаны в
        конвейере выше, здесь дублируем дёшево на всякий случай. Лимит ОБЩЕГО
        объёма (500 МБ) для сирот НЕ проверяем: он мягкий и всё равно
        перепроверяется при следующем открытии карточки; жёстко ронять уже
        прочитанную с диска картинку из-за суммарного капа здесь неоправданно."""
        if not data or len(data) > self._MAX_IMAGE_BYTES:
            return False
        if size is None or size[0] * size[1] > self._MAX_IMAGE_PIXELS:
            return False
        return True

    async def _upload_pipeline(self, item, path, gen, account_id=None):
        """Async-конвейер загрузки картинки с диска. Разные операции — в разных
        потоках пула (run_in_executor), UI-поток лишь рисует результат:
          1) чтение файла           → поток;
          2) сжатие + декод миниатюры → поток (самый тяжёлый CPU);
          3) проверка лимитов + рисование → UI-поток (дёшево).

        gen — поколение карточки на момент запуска: если оно сменилось (пользователь
        переключил аккаунт / lock / restore), результат в текущую карточку не
        применяем (M65-02), а отдаём его аккаунту account_id через orphan-handler
        (M7-05) — загрузка больше не теряется при переключении."""
        loop = asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(
                None, _read_file, path, self._MAX_IMAGE_BYTES)
        except FileTooLargeError:
            if gen == self._data_gen and item in self.items:
                self._remove_item_silent(item)
            mb = self._MAX_IMAGE_BYTES // (1024 * 1024)
            _warn(self.config, self, "Слишком большой файл",
                  f"Файл больше {mb} МБ и не будет загружен.")
            return
        except OSError as e:
            if gen == self._data_gen and item in self.items:
                self._remove_item_silent(item)
            _warn(self.config, self, "Ошибка", f"Не удалось прочитать файл:\n{e}")
            return
        data, size, thumb = await loop.run_in_executor(
            None, _prepare_image_bytes, data, self._downscale_enabled())
        if gen != self._data_gen or item not in self.items:
            # Карточку сменили / элемент удалили — отдаём результат своему аккаунту
            # (M7-05), не теряя загрузку. Проверки, зависящие от аккаунта, делает
            # уже handler; здесь — только аккаунт-независимая валидность.
            if self._accept_orphan(data, size):
                self._emit_orphan(account_id, self._orphan_desc(item), data)
            return
        if not self._accept_image_checked(data, size, pending_placeholder=True):
            self._remove_item_silent(item)   # предупреждение уже показал accept
            return
        item["bytes"] = data
        self._apply_thumb(item["thumb"], thumb)

    def _start_thumb_preload(self):
        """Запустить async-предпросмотр миниатюр ленивых элементов (bytes=None).

        Чтение BLOB и декод выполняются в потоках пула (run_in_executor) — UI не
        блокируется. Элементы обрабатываются по одному (память ограничена одним
        изображением «в полёте»); прочитанные байты кэшируем для просмотра/
        сохранения без повторного чтения. _preload_gen гасит цепочку при смене
        карточки."""
        if self._image_loader is None:
            return
        self._preload_gen += 1
        gen = self._preload_gen
        # Токен сессии на весь запуск предпросмотра: если сессия сменится (lock/
        # restore), async-чтение поднимет StaleSessionError и цепочка прервётся.
        session = self._session_provider() if self._session_provider else None
        pending = [it for it in self.items
                   if it["bytes"] is None and it["image_id"] is not None]
        util.fire(self._preload_pipeline(pending, gen, session))

    async def _preload_pipeline(self, items, gen, session):
        loop = asyncio.get_running_loop()
        for item in items:
            if gen != self._preload_gen:
                return                       # карточку переключили — цепочка устарела
            loader = self._image_loader
            if (item not in self.items or item["bytes"] is not None
                    or item["image_id"] is None or loader is None):
                continue
            # Чтение BLOB — через координатор БД (session-токен) либо, если он не
            # внедрён, в потоке пула (load_gallery_image потокобезопасен под RLock).
            if self._async_reader is not None:
                data = await self._async_reader(item["image_id"], session)
            else:
                data = await loop.run_in_executor(None, loader, item["image_id"])
            if gen != self._preload_gen or item not in self.items:
                return
            if data is None:
                self._apply_thumb(item["thumb"], None)
                continue
            # Декод миниатюры — в потоке.
            thumb = await loop.run_in_executor(None, _decode_thumb, data, 100)
            if gen != self._preload_gen or item not in self.items:
                return
            item["bytes"] = data             # кэш: просмотр/сохранение без чтения
            self._apply_thumb(item["thumb"], thumb)

    def _remove_item_silent(self, item):
        """Убрать элемент галереи без подтверждения (для отклонённой загрузки)."""
        for i, it in enumerate(self.items):
            if it is item:
                self.items.pop(i)
                break
        w = item["widget"]
        w.hide()
        self.items_layout.removeWidget(w)
        w.deleteLater()

    def paste_image(self):
        """Вставка из буфера. Как и загрузка с диска, идёт через отслеживаемую
        задачу (M65-01): Save её дождётся, а завершившаяся вставка проверит
        поколение карточки и не попадёт в чужой аккаунт при переключении."""
        # Аккаунт снимаем ДО первого await (M7-05) — см. _queue_file_load.
        aid = self._current_account_id()
        self._track_upload(self._paste_pipeline(self._data_gen, aid))

    async def _paste_pipeline(self, gen, account_id=None):
        loop = asyncio.get_running_loop()
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        data = None

        # 1. Изображение в буфере (браузер, Paint, скриншот)
        if mime_data.hasImage():
            image = clipboard.image()
            if not image.isNull():
                data = await loop.run_in_executor(None, self._image_to_png_bytes, image)
        # 2. Файл картинки (из Проводника)
        elif mime_data.hasUrls():
            for url in mime_data.urls():
                if url.isLocalFile() and url.toLocalFile().lower().endswith(_IMAGE_EXTS):
                    data = self._read_file_bytes(url.toLocalFile())
                    break
        # 3. Текстовый путь к файлу
        elif mime_data.hasText():
            text = mime_data.text().strip()
            if text.lower().endswith(_IMAGE_EXTS) and os.path.exists(text):
                data = self._read_file_bytes(text)

        if data:
            # Единое поведение с загрузкой с диска: сжимаем (если включено) и
            # снимаем размер в фоне (M6-02), затем добавляем.
            data, size, _ = await loop.run_in_executor(
                None, _prepare_image_bytes, data, self._downscale_enabled())
            if gen != self._data_gen:
                # Карточку сменили — не добавляем в чужую, а отдаём своему аккаунту
                # (M7-05). desc пустой: у вставки нет placeholder-элемента.
                if self._accept_orphan(data, size):
                    self._emit_orphan(account_id, "", data)
                return
            if self._accept_image_checked(data, size):
                self.add_item(data)
        else:
            _warn(
                self.config, self, "Буфер обмена",
                "В буфере нет изображения!\nСкопируйте картинку или файл картинки.",
            )

    def add_item(self, image_bytes, desc="", image_id=None, blob_size=None):
        """Добавить элемент галереи.

        image_bytes — байты изображения (уже в памяти); если None и image_id
        задан — показываем placeholder ленивой загрузки (BLOB загрузится при
        клике или при save через get_data).
        blob_size — размер BLOB ленивого элемента в БД (для учёта в лимите
        общего объёма, M7-03); None для новых/уже-в-памяти картинок.
        """
        item_widget = QWidget(self)
        # Рамка элемента — полутон между текстом и фоном темы (H-10): видна и на
        # тёмных, и на светлых темах, вместо жёсткого #808080.
        _text, _bg = self._theme_colors()
        item_widget.setStyleSheet(
            f"border: 1px solid {mix(_text, _bg, 0.5)}; padding: 5px;")
        h_layout = QHBoxLayout(item_widget)

        thumb_label = QLabel()
        thumb_label.setFixedSize(100, 100)
        thumb_label.setAlignment(Qt.AlignCenter)
        thumb_label.setCursor(Qt.PointingHandCursor)

        if image_bytes is not None:
            # Обычный режим — байты уже есть, рендерим миниатюру.
            thumb_img = self._decode_image(image_bytes, bound=100)
            if thumb_img is not None:
                thumb_label.setStyleSheet(self._thumb_css())
                thumb_label.setPixmap(QPixmap.fromImage(thumb_img).scaled(
                    100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                thumb_label.setStyleSheet(self._thumb_css("full"))
                thumb_label.setText("[ нет\nпревью ]")
        else:
            # Ленивый режим — BLOB ещё не загружен, показываем placeholder.
            thumb_label.setStyleSheet(self._thumb_css("dim"))
            thumb_label.setText("[ фото ]")

        h_layout.addWidget(thumb_label)

        v_layout = QVBoxLayout()
        desc_edit = QLineEdit(desc)
        desc_edit.setPlaceholderText("Описание картинки...")
        desc_edit.setReadOnly(not self._editable)
        v_layout.addWidget(desc_edit)

        btn_row = QHBoxLayout()
        # Родитель задаём СРАЗУ: setVisible() на виджете без родителя показывает
        # его как отдельное top-level окно со стандартной рамкой (баг: пустое
        # окно мелькает при добавлении картинки / переключении аккаунта).
        del_btn = QPushButton("[X]", item_widget)
        del_btn.setFixedWidth(40)
        del_btn.setVisible(self._editable)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        v_layout.addLayout(btn_row)

        h_layout.addLayout(v_layout)
        self.items_layout.addWidget(item_widget)

        item = {"bytes": image_bytes, "desc": desc_edit, "del": del_btn,
                "widget": item_widget, "thumb": thumb_label, "image_id": image_id,
                "blob_size": blob_size}
        self.items.append(item)

        del_btn.clicked.connect(lambda: self.remove_item(item_widget))

        # ЛКМ — увеличенный просмотр (с ленивой загрузкой если нужно).
        thumb_label.mousePressEvent = lambda e, it=item: (
            self._on_thumb_click(it) if e.button() == Qt.LeftButton else None)
        # ПКМ по миниатюре — экспорт изображения (в файл / в буфер обмена).
        thumb_label.setContextMenuPolicy(Qt.CustomContextMenu)
        thumb_label.customContextMenuRequested.connect(
            lambda pos, it=item, lbl=thumb_label: self._show_image_menu_lazy(lbl, pos, it))

    def remove_item(self, widget):
        if not _confirm(self.config, self, "Удаление",
                        "Удалить это изображение из галереи?"):
            return
        for i, it in enumerate(self.items):
            if it["widget"] == widget:
                self.items.pop(i)
                break
        widget.hide()
        self.items_layout.removeWidget(widget)
        widget.deleteLater()

    async def _load_lazy_bytes_async(self, item):
        """Асинхронно загрузить BLOB ленивого элемента (image_id задан, bytes=None)
        и закэшировать в item — для просмотра/экспорта по клику. Чтение вынесено
        из UI-потока (H-7): на больших BLOB синхронный loader вешал интерфейс.

        Как и предпросмотр (_preload_pipeline), читаем через координатор БД с
        зафиксированным токеном сессии (session-провайдер), иначе — в потоке пула.
        Токен снимается ОДИН раз: смена сессии (lock/restore) прерывает чтение.
        Возвращает bytes или None (загрузчик не задан / нет данных / устарело)."""
        if item["bytes"] is not None:
            return item["bytes"]
        if item["image_id"] is None or self._image_loader is None:
            return None
        loop = asyncio.get_running_loop()
        session = self._session_provider() if self._session_provider else None
        image_id = item["image_id"]
        if self._async_reader is not None:
            data = await self._async_reader(image_id, session)
        else:
            loader = self._image_loader
            data = await loop.run_in_executor(None, loader, image_id)
        if item not in self.items:
            return None                          # элемент удалили за время чтения
        if data is not None:
            item["bytes"] = data
            self._apply_thumb(item["thumb"], self._decode_image(data, bound=100))
        return data

    def _set_thumb_loading(self, item, loading):
        """Лёгкое состояние «загрузка» на миниатюре ленивого элемента, пока идёт
        async-чтение BLOB по клику (H-7). Возвращаем placeholder-текст, только если
        байты так и не появились (иначе миниатюру уже нарисовал _apply_thumb)."""
        lbl = item["thumb"]
        if loading:
            lbl.setStyleSheet(self._thumb_css("dim"))
            lbl.setText("[ … ]")
        elif item["bytes"] is None:
            lbl.setStyleSheet(self._thumb_css("dim"))
            lbl.setText("[ фото ]")

    def _on_thumb_click(self, item):
        """Обработчик ЛКМ по миниатюре: если BLOB уже в памяти — показываем сразу,
        иначе запускаем async-загрузку (UI не виснет, H-7)."""
        if item["bytes"] is not None:
            self.show_full_image(item["bytes"])
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
        """Контекстное меню миниатюры с ленивой загрузкой перед экспортом (async —
        чтение BLOB не блокирует UI, H-7)."""
        menu = QMenu(self)
        act_file = menu.addAction("Экспорт в файл…")
        act_clip = menu.addAction("Экспорт в буфер обмена")
        chosen = menu.exec(label.mapToGlobal(pos))
        if chosen not in (act_file, act_clip):
            return
        if item["bytes"] is not None:
            self._export_menu_action(chosen, act_file, item["bytes"])
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
        """Экспорт изображения по выбранному пункту меню (файл / буфер обмена)."""
        if chosen == act_file:
            self._export_image_to_file(data)
        else:
            self._export_image_to_clipboard(data)

    def show_full_image(self, image_bytes):
        img = self._decode_image(image_bytes, bound=1600)
        label = QLabel()
        if img is not None:
            scaled = QPixmap.fromImage(img).scaled(
                800, 600, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            label.setPixmap(scaled)
        else:
            label.setText("Не удалось отобразить изображение\n"
                          "(повреждено или превышает лимит памяти).")
            label.setAlignment(Qt.AlignCenter)
        # Единый стиль программы; откат на обычный QDialog, если config недоступен.
        if self.config is not None:
            from theme import ThemedDialog
            dialog = ThemedDialog(self.config, self)
            dialog.setWindowTitle("Просмотр изображения")
            dialog.body.addWidget(label)
        else:
            dialog = QDialog(self)
            dialog.setWindowTitle("Просмотр изображения")
            QVBoxLayout(dialog).addWidget(label)
        dialog.exec()

    @staticmethod
    def _guess_ext(data):
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

    def _export_image_to_file(self, image_bytes):
        ext = self._guess_ext(image_bytes)
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить изображение", "image" + ext,
            f"Изображение (*{ext});;Все файлы (*)")
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(image_bytes)
        except OSError as e:
            _warn(self.config, self, "Ошибка", f"Не удалось сохранить файл:\n{e}")
            return
        _warn(self.config, self, "Готово", f"Изображение сохранено:\n{path}")

    def _export_image_to_clipboard(self, image_bytes):
        img = QImage()
        if not img.loadFromData(image_bytes):
            _warn(self.config, self, "Ошибка", "Не удалось прочитать изображение.")
            return
        QApplication.clipboard().setImage(img)
        _warn(self.config, self, "Готово", "Изображение скопировано в буфер обмена.")

    def get_data(self):
        """Возвращает список элементов галереи для сохранения в БД (контракт H-6).

        Каждый элемент — {"desc": str, "data": bytes|None, "image_id": int|None}:
          * ленивый, ещё не загруженный (bytes=None, image_id задан) → отдаём
            data=None + image_id: БД сохранит существующий BLOB, не удаляя его
            (раньше такой элемент молча пропускался — потеря данных, H-6). BLOB
            здесь НЕ дочитываем синхронно (это фризило UI, H-7);
          * загруженный/новый (bytes есть) → отдаём data=bytes; если у элемента
            есть image_id (перезалитая картинка) — он тоже идёт, иначе вставка
            новой строки.
        Элемент без bytes и без image_id (пустой placeholder) пропускаем."""
        result = []
        for it in self.items:
            data = it["bytes"]
            image_id = it["image_id"]
            if data is None and image_id is None:
                continue                         # нечего сохранять
            result.append({"desc": it["desc"].text(),
                           "data": data, "image_id": image_id})
        return result

    def assign_saved_ids(self, saved_ids):
        """Присваивает элементам id строк БД после успешного сохранения.

        saved_ids — результат Database.save_account*: список id в порядке и
        составе get_data() (пустые placeholder'ы пропущены). Без этого новая
        картинка не знала бы свой id, и следующее сохранение в той же сессии
        пересоздавало бы её строку (лишняя перезапись BLOB). Элементы галереи
        на время записи заблокированы (set_all_editable(False)), поэтому состав
        не меняется; на случай гонки сверяем длину и молча выходим."""
        savable = [it for it in self.items
                   if it["bytes"] is not None or it["image_id"] is not None]
        if len(savable) != len(saved_ids):
            return
        for it, new_id in zip(savable, saved_ids):
            if new_id is not None:
                it["image_id"] = new_id

    def set_data(self, data):
        """Загрузить список элементов галереи.

        Каждый элемент может быть:
          {"data": bytes, "desc": str}                  — байты уже в памяти
          {"image_id": int, "desc": str, "data": None}  — ленивый (только из БД)
        image_id читаем как из ключа "id" (прямой ответ load_account), так и из
        "image_id" (после round-trip через кеш правок to_storage/from_storage,
        H-6) — чтобы контракт «оставить существующий BLOB» не терялся."""
        # Смена карточки: поднимаем поколение данных, чтобы ещё не завершённый
        # импорт (upload/paste) прежней карточки не попал в загружаемую карточку
        # общего виджета (M65-01/M65-02). Сам импорт НЕ отменяем (M7-05): он
        # доживает в фоне, а его результат уйдёт своему аккаунту через orphan-
        # handler (см. _upload_pipeline/_paste_pipeline). Отмена осталась только в
        # cancel_all_tasks — там сессия БД меняется (lock/restore/close) и
        # продолжать импорт нельзя.
        self._data_gen += 1
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
                # Байты уже есть — обычный путь.
                self.add_item(img_data, desc, image_id=img_id)
            elif img_id is not None:
                # Ленивый элемент из БД: placeholder, загрузка по запросу.
                # blob_size — размер BLOB для учёта в лимите общего объёма (M7-03),
                # пока сами байты не подгружены (bytes=None).
                self.add_item(None, desc, image_id=img_id, blob_size=blob_size)
            # Элементы без data и без id игнорируются.

    def set_editable(self, editable):
        self._editable = editable
        self.upload_btn.setVisible(editable)
        self.paste_btn.setVisible(editable)
        for it in self.items:
            it["desc"].setReadOnly(not editable)
            it["del"].setVisible(editable)


class IntervalField(QWidget):
    """Поле «Сменять пароль каждые N дней». Значение 0 = срок не задан (None)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.spin = QSpinBox()
        self.spin.setRange(0, 3650)
        self.spin.setSuffix(" дн.")
        self.spin.setSpecialValueText("не задано")  # отображается при значении 0
        # Чуть шире, чтобы «не задано» не обрезалось (внутренние отступы темы).
        self.spin.setMinimumWidth(150)
        self.spin.setReadOnly(True)
        self.spin.setButtonSymbols(QSpinBox.NoButtons)
        layout.addWidget(self.spin)
        layout.addStretch()

    def set_value(self, days):
        self.spin.setValue(int(days) if days else 0)

    def get_value(self):
        v = self.spin.value()
        return v if v > 0 else None

    def set_editable(self, editable):
        self.spin.setReadOnly(not editable)
        self.spin.setButtonSymbols(QSpinBox.UpDownArrows if editable else QSpinBox.NoButtons)


class LinkedAccountsWidget(QWidget):
    """Список связанных аккаунтов. Клик по аккаунту — навигация к нему в дереве."""

    navigate_requested = Signal(int)   # account_id
    add_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(5)

        self.add_btn = QPushButton("+ СВЯЗАТЬ АККАУНТ")
        self.add_btn.clicked.connect(self.add_requested.emit)
        self.layout.addWidget(self.add_btn)

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(3)
        self.layout.addLayout(self.rows_layout)

        self.empty_label = QLabel("(нет связанных аккаунтов)")
        self.layout.addWidget(self.empty_label)
        self.layout.addStretch()

        # (account_id, name, row_widget)
        self.items = []
        self._editable = False

    def _add_row(self, account_id, name):
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)

        link_btn = QPushButton(name)
        link_btn.setToolTip("Перейти к связанному аккаунту")
        link_btn.clicked.connect(lambda: self.navigate_requested.emit(account_id))
        h.addWidget(link_btn, 1)

        del_btn = QPushButton("[X]", row)          # родитель сразу — см. add_item
        del_btn.setFixedWidth(40)
        del_btn.setVisible(self._editable)
        del_btn.clicked.connect(lambda: self._remove_row(row))
        h.addWidget(del_btn)

        self.rows_layout.addWidget(row)
        # Прямая ссылка на кнопку удаления (L-9) — без layout().itemAt(...).
        self.items.append((account_id, name, row, del_btn))
        self._update_empty()

    def _remove_row(self, row):
        for i, (_aid, _name, w, _btn) in enumerate(self.items):
            if w == row:
                self.items.pop(i)
                break
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self._update_empty()

    def _update_empty(self):
        self.empty_label.setVisible(not self.items)

    def set_data(self, links):
        for _aid, _name, w, _btn in self.items:
            w.hide()
            self.rows_layout.removeWidget(w)
            w.deleteLater()
        self.items.clear()
        for link in links:
            self._add_row(link["id"], link["name"])
        self._update_empty()

    def get_data(self):
        return [aid for aid, _name, _w, _btn in self.items]

    def set_editable(self, editable):
        self._editable = editable
        self.add_btn.setVisible(editable)
        for _aid, _name, _row, del_btn in self.items:
            del_btn.setVisible(editable)