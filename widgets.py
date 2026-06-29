import os
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit,
                               QPushButton, QLabel, QDateEdit, QDateTimeEdit,
                               QTextEdit, QFileDialog, QDialog, QMessageBox,
                               QApplication, QSpinBox, QMenu)
from PySide6.QtCore import Signal, Qt, QDate, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QPixmap, QImage, QImageReader
from theme import themed_info, themed_confirm


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
    copy_signal = Signal()
    
    def __init__(self, is_datetime=False, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        self._is_datetime = is_datetime
        if is_datetime:
            self.date_widget = QDateTimeEdit()
            self.date_widget.setDisplayFormat("yyyy-MM-dd HH:mm")
            self.format = "yyyy-MM-dd HH:mm"
        else:
            self.date_widget = QDateEdit()
            self.date_widget.setDisplayFormat("yyyy-MM-dd")
            self.format = "yyyy-MM-dd"

        # Календарь-попап для удобного выбора даты; ввод цифрами с клавиатуры
        # заменяет значение в активной секции (поведение QDateTimeEdit по умолчанию).
        self.date_widget.setCalendarPopup(True)
        self.date_widget.setReadOnly(True)
        # «Не задано»: минимально возможное значение показываем как пустое, чтобы
        # отсутствие даты не подменялось сегодняшним числом (ложные данные).
        self.date_widget.setSpecialValueText("не задано")
        layout.addWidget(self.date_widget)
        
        self.copy_btn = QPushButton("[КОП]")
        self.copy_btn.setFixedWidth(60)
        self.copy_btn.clicked.connect(self.do_copy)
        layout.addWidget(self.copy_btn)
        
    def do_copy(self):
        if self._is_unset():
            return
        text = self.date_widget.dateTime().toString(self.format)
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()

    def _is_unset(self):
        """True, если показано «не задано» (значение равно минимальному)."""
        if self._is_datetime:
            return self.date_widget.dateTime() == self.date_widget.minimumDateTime()
        return self.date_widget.date() == self.date_widget.minimumDate()

    def _set_unset(self):
        if self._is_datetime:
            self.date_widget.setDateTime(self.date_widget.minimumDateTime())
        else:
            self.date_widget.setDate(self.date_widget.minimumDate())

    def set_date(self, date):
        """Принимает QDate, QDateTime, None или строку. None/пусто → «не задано»."""
        from PySide6.QtCore import QTime, QDateTime
        if date is None:
            self._set_unset()
        elif isinstance(date, QDateTime):
            self.date_widget.setDateTime(date)
        elif hasattr(date, 'year'):  # QDate
            if self._is_datetime:
                self.date_widget.setDateTime(QDateTime(date, QTime(0, 0)))
            else:
                self.date_widget.setDate(date)
        else:
            # Неизвестный формат — считаем «не задано», а не «сегодня».
            self._set_unset()

    def get_date(self):
        """QDateTime или None, если дата не задана."""
        if self._is_unset():
            return None
        return self.date_widget.dateTime()
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
        self.rows.append((q_edit, a_edit))
        self._update_empty()

    def remove_row(self, row_widget):
        if not _confirm(self.config, self, "Удаление",
                        "Удалить этот секретный вопрос?"):
            return
        for i, (q, a) in enumerate(self.rows):
            if q.parent() == row_widget:
                self.rows.pop(i)
                break
        self.layout.removeWidget(row_widget)
        row_widget.deleteLater()
        self._update_empty()

    def get_data(self):
        # Не сохраняем строку, если оба связанных поля пустые.
        return [{"q": q.text(), "a": a.text()} for q, a in self.rows
                if q.text().strip() or a.text().strip()]

    def set_data(self, data):
        for row_widget in [q.parent() for q, a in self.rows]:
            self.layout.removeWidget(row_widget)
            row_widget.deleteLater()
        self.rows.clear()
        for item in data: self.add_row(item.get("q", ""), item.get("a", ""))
        self._update_empty()
    def set_editable(self, editable):
        self._editable = editable
        self.add_btn.setVisible(editable)
        for q, a in self.rows:
            q.setReadOnly(not editable)
            a.setReadOnly(not editable)
            del_btn = q.parent().layout().itemAt(2).widget()
            del_btn.setVisible(editable)

class CodeListWidget(QWidget):
    copy_signal = Signal()

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(5)
        self.rows = []
        self._editable = False

        self.add_btn = QPushButton("+ ДОБАВИТЬ КОД")
        self.add_btn.clicked.connect(lambda: self.add_code())
        self.layout.addWidget(self.add_btn)
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
        self.rows.append((code_edit, row_widget))
        self._update_empty()

    def copy_code(self, text):
        if text:
            QApplication.clipboard().setText(text)
            self.copy_signal.emit()
            
    def remove_code(self, row_widget):
        if not _confirm(self.config, self, "Удаление",
                        "Удалить этот код / резервный ключ?"):
            return
        for i, (edit, widget) in enumerate(self.rows):
            if widget == row_widget:
                self.rows.pop(i)
                break
        self.layout.removeWidget(row_widget)
        row_widget.deleteLater()
        self._update_empty()

    def get_data(self):
        # Не сохраняем пустые коды.
        return [edit.text() for edit, _ in self.rows if edit.text().strip()]

    def set_data(self, data):
        for _, widget in self.rows:
            self.layout.removeWidget(widget)
            widget.deleteLater()
        self.rows.clear()
        for code in data: self.add_code(code)
        self._update_empty()
        
    def set_editable(self, editable):
        self._editable = editable
        self.add_btn.setVisible(editable)
        for code_edit, widget in self.rows:
            code_edit.setReadOnly(not editable)
            del_btn = widget.layout().itemAt(2).widget()
            del_btn.setVisible(editable)

class GalleryWidget(QWidget):
    """Галерея изображений. Хранит сами байты картинок (для записи в BLOB),
    а не пути к файлам — чтобы документ был самодостаточным."""

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

    def _read_file_bytes(self, path):
        try:
            with open(path, "rb") as f:
                return f.read()
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
        loader_fn(image_id: int) -> bytes | None — вызывается когда пользователь
        кликает на placeholder (просмотр) или при сохранении (get_data)."""
        self._image_loader = loader_fn

    def _local_bytes(self):
        """Суммарный объём картинок в текущей (редактируемой) карточке.
        Элементы с bytes=None (ещё не загружены) не учитываются."""
        return sum(len(it["bytes"]) for it in self.items if it["bytes"] is not None)

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
        """Проверяет ДОБАВЛЯЕМОЕ изображение: размер файла, разрешение (по
        метаданным, до декодирования — защита от «бомбы»), число картинок на
        аккаунт и суммарный объём по базе. Возвращает True, если можно сохранить.
        К уже сохранённым в БД изображениям не применяется (см. add_item)."""
        if not data:
            return False
        if len(data) > self._MAX_IMAGE_BYTES:
            mb = self._MAX_IMAGE_BYTES // (1024 * 1024)
            _warn(self.config, self, "Слишком большой файл",
                  f"Изображение больше {mb} МБ и не будет добавлено.")
            return False
        # Разрешение определяем ДО декодирования: огромная по пикселям картинка
        # (decompression bomb) отклоняется, не разворачиваясь в память целиком.
        size = self._read_image_size(data)
        if size is None:
            _warn(self.config, self, "Ошибка",
                  "Файл не распознан как изображение.")
            return False
        if size[0] * size[1] > self._MAX_IMAGE_PIXELS:
            mp = self._MAX_IMAGE_PIXELS // 1_000_000
            _warn(self.config, self, "Слишком большое изображение",
                  f"Разрешение превышает {mp} Мп и не будет добавлено.")
            return False
        if len(self.items) >= self._MAX_IMAGES_PER_ACCOUNT:
            _warn(self.config, self, "Слишком много изображений",
                  f"На один аккаунт допускается не более "
                  f"{self._MAX_IMAGES_PER_ACCOUNT} изображений.")
            return False
        provider = getattr(self, "_other_bytes_provider", None)
        other = 0
        if provider is not None:
            try:
                other = provider()
            except Exception:
                other = 0
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
        if path:
            data = self._read_file_bytes(path)
            if data and self._accept_image(data):
                self.add_item(data)

    def paste_image(self):
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        data = None

        # 1. Изображение в буфере (браузер, Paint, скриншот)
        if mime_data.hasImage():
            image = clipboard.image()
            if not image.isNull():
                data = self._image_to_png_bytes(image)
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
            if self._accept_image(data):
                self.add_item(data)
        else:
            _warn(
                self.config, self, "Буфер обмена",
                "В буфере нет изображения!\nСкопируйте картинку или файл картинки.",
            )

    def add_item(self, image_bytes, desc="", image_id=None):
        """Добавить элемент галереи.

        image_bytes — байты изображения (уже в памяти); если None и image_id
        задан — показываем placeholder ленивой загрузки (BLOB загрузится при
        клике или при save через get_data).
        """
        item_widget = QWidget(self)
        item_widget.setStyleSheet("border: 1px solid #808080; padding: 5px;")
        h_layout = QHBoxLayout(item_widget)

        thumb_label = QLabel()
        thumb_label.setFixedSize(100, 100)
        thumb_label.setAlignment(Qt.AlignCenter)
        thumb_label.setCursor(Qt.PointingHandCursor)

        if image_bytes is not None:
            # Обычный режим — байты уже есть, рендерим миниатюру.
            thumb_img = self._decode_image(image_bytes, bound=100)
            if thumb_img is not None:
                thumb_label.setStyleSheet("background-color: #333;")
                thumb_label.setPixmap(QPixmap.fromImage(thumb_img).scaled(
                    100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                thumb_label.setStyleSheet("background-color: #333; color: #FFC400;")
                thumb_label.setText("[ нет\nпревью ]")
        else:
            # Ленивый режим — BLOB ещё не загружен, показываем placeholder.
            thumb_label.setStyleSheet("background-color: #333; color: #AAAAAA;")
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
                "widget": item_widget, "thumb": thumb_label, "image_id": image_id}
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

    def _load_lazy_bytes(self, item):
        """Загружает BLOB для ленивого элемента (image_id задан, bytes=None).
        После загрузки кэширует байты в item и обновляет миниатюру.
        Возвращает bytes или None (если загрузчик не задан / нет данных)."""
        if item["bytes"] is not None:
            return item["bytes"]
        if item["image_id"] is None or self._image_loader is None:
            return None
        data = self._image_loader(item["image_id"])
        if data is not None:
            item["bytes"] = data
            # Обновить миниатюру после загрузки.
            lbl = item["thumb"]
            thumb_img = self._decode_image(data, bound=100)
            if thumb_img is not None:
                lbl.setStyleSheet("background-color: #333;")
                lbl.setText("")
                lbl.setPixmap(QPixmap.fromImage(thumb_img).scaled(
                    100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                lbl.setStyleSheet("background-color: #333; color: #FFC400;")
                lbl.setText("[ нет\nпревью ]")
        return data

    def _on_thumb_click(self, item):
        """Обработчик ЛКМ по миниатюре: загружает BLOB при необходимости, затем
        показывает полное изображение."""
        data = self._load_lazy_bytes(item)
        if data:
            self.show_full_image(data)

    def _show_image_menu_lazy(self, label, pos, item):
        """Контекстное меню миниатюры с ленивой загрузкой перед экспортом."""
        menu = QMenu(self)
        act_file = menu.addAction("Экспорт в файл…")
        act_clip = menu.addAction("Экспорт в буфер обмена")
        chosen = menu.exec(label.mapToGlobal(pos))
        if chosen in (act_file, act_clip):
            data = self._load_lazy_bytes(item)
            if not data:
                _warn(self.config, self, "Ошибка", "Изображение недоступно.")
                return
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
        """Возвращает список {"data": bytes, "desc": str} для сохранения в БД.
        Ленивые элементы (bytes=None, image_id задан) загружаются через loader
        прямо здесь — чтобы save_account получил полный BLOB."""
        result = []
        for it in self.items:
            data = it["bytes"]
            if data is None and it["image_id"] is not None:
                data = self._load_lazy_bytes(it)
            if data is not None:
                result.append({"data": data, "desc": it["desc"].text()})
        return result

    def set_data(self, data):
        """Загрузить список элементов галереи.

        Каждый элемент может быть:
          {"data": bytes, "desc": str}            — байты уже в памяти
          {"id": int, "desc": str, "data": None}  — ленивый (только из БД)
        """
        for it in self.items:
            self.items_layout.removeWidget(it["widget"])
            it["widget"].deleteLater()
        self.items.clear()
        for item in data:
            img_id = item.get("id")
            img_data = item.get("data")
            desc = item.get("desc", "")
            if img_data is not None:
                # Байты уже есть — обычный путь.
                self.add_item(img_data, desc, image_id=img_id)
            elif img_id is not None:
                # Ленивый элемент из БД: placeholder, загрузка по запросу.
                self.add_item(None, desc, image_id=img_id)
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
        self.items.append((account_id, name, row))
        self._update_empty()

    def _remove_row(self, row):
        for i, (aid, name, w) in enumerate(self.items):
            if w == row:
                self.items.pop(i)
                break
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self._update_empty()

    def _update_empty(self):
        self.empty_label.setVisible(not self.items)

    def set_data(self, links):
        for _, _, w in self.items:
            w.hide()
            self.rows_layout.removeWidget(w)
            w.deleteLater()
        self.items.clear()
        for link in links:
            self._add_row(link["id"], link["name"])
        self._update_empty()

    def get_data(self):
        return [aid for aid, _, _ in self.items]

    def set_editable(self, editable):
        self._editable = editable
        self.add_btn.setVisible(editable)
        for _, _, row in self.items:
            row.layout().itemAt(1).widget().setVisible(editable)