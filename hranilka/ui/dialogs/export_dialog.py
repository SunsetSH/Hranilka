"""Диалог экспорта в TXT/CSV/HTML/XLSX (вынесен из dialogs.py, этап 4)."""
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QGroupBox,
                               QHBoxLayout, QLabel, QPushButton, QRadioButton,
                               QVBoxLayout)

from hranilka.data import export
from hranilka.ui.theme import ThemedDialog, themed_info


def theme_dict(config):
    """Словарь цветов/шрифта из настроек — для оформления HTML-экспорта."""
    return {
        "font": config.get("font", "Consolas"),
        "font_size": config.get("font_size", 14),
        "text_color": config.get("text_color", "#000000"),
        "main_bg_color": config.get("main_bg_color", "#F0F0F0"),
        "tree_bg_color": config.get("tree_bg_color", "#FFFFFF"),
    }


class ExportDialog(ThemedDialog):
    """Окно экспорта поддерева/всей базы в TXT/CSV/HTML/XLSX.

    tree — структура из Database.export_subtree(); title — что экспортируется
    (путь узла или «Вся база»)."""

    def __init__(self, config, tree, title="Вся база", parent=None):
        super().__init__(config, parent)
        self.setWindowTitle("Экспорт")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._tree = tree
        self._title = title

        lay = self.body
        what = QLabel(f"Что: {title}")
        what.setWordWrap(True)
        lay.addWidget(what)

        fmt_group = QGroupBox("Формат")
        fl = QVBoxLayout(fmt_group)
        self._fmt_btns = QButtonGroup(self)
        formats = [
            ("html", "HTML — оформленный документ с картинками"),
            ("xlsx", "XLSX — таблица Excel"),
            ("csv", "CSV — таблица (текстовая, для переноса)"),
            ("txt", "TXT — простой текст (блокнот)"),
        ]
        for i, (key, label) in enumerate(formats):
            rb = QRadioButton(label)
            rb.setProperty("fmt", key)
            if i == 0:
                rb.setChecked(True)
            self._fmt_btns.addButton(rb)
            fl.addWidget(rb)
        lay.addWidget(fmt_group)

        opt_group = QGroupBox("Что включить")
        ol = QVBoxLayout(opt_group)
        self._chk_basic = QCheckBox("Включить базовые данные, логин и пароль")
        self._chk_other = QCheckBox("Включить остальные поля")
        self._chk_gallery = QCheckBox("Включить галерею (изображения и их описания)")
        for c in (self._chk_basic, self._chk_other, self._chk_gallery):
            c.setChecked(True)
            ol.addWidget(c)
        lay.addWidget(opt_group)

        warn = QLabel(
            "⚠ Экспорт сохраняет выбранные данные в ОТКРЫТОМ виде в обычный "
            "файл на диске. Храните файл в надёжном месте.")
        warn.setWordWrap(True)
        # Цвет — из темы (как у остального текста), но жирным для акцента.
        # Литеральный fallback убран (H-10): Config всегда содержит text_color,
        # а '#000000' не совпадал с фактическим дефолтом настроек.
        warn.setStyleSheet(f"color: {self.config.get('text_color')}; "
                           "font-weight: bold;")
        lay.addWidget(warn)

        # Подключаем после создания галочек: обработчик обращается к ним.
        self._fmt_btns.buttonToggled.connect(self._on_format_changed)
        self._on_format_changed()  # начальное состояние (галерея для TXT/CSV)

        row = QHBoxLayout()
        row.addStretch()
        self._ok = QPushButton("Экспортировать…")
        self._ok.setDefault(True)
        self._ok.clicked.connect(self._do_export)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        row.addWidget(self._ok)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _current_format(self):
        return self._fmt_btns.checkedButton().property("fmt")

    def _on_format_changed(self, *_):
        # Картинки помещаются только в HTML. Для остальных форматов галочка
        # галереи становится неактивной (приглушённой) с пояснением.
        supports_img = self._current_format() in ("html",)
        self._chk_gallery.setEnabled(supports_img)
        if supports_img:
            self._chk_gallery.setText("Включить галерею (изображения и их описания)")
        else:
            selected = self._current_format().upper()
            self._chk_gallery.setText(
                f"Включить галерею — недоступно для {selected}")

    def _do_export(self):
        fmt = self._current_format()
        func, ext, flt = export.FORMATS[fmt]
        if not (self._chk_basic.isChecked() or self._chk_other.isChecked()
                or (self._chk_gallery.isEnabled() and self._chk_gallery.isChecked())):
            themed_info(self.config, self, "Экспорт",
                        "Выберите хотя бы один пункт в разделе «Что включить».")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить экспорт", "hranilka_export" + ext, flt)
        if not path:
            return
        if not path.lower().endswith(ext):
            path += ext
        opts = export.Options(
            include_basic=self._chk_basic.isChecked(),
            include_other=self._chk_other.isChecked(),
            include_gallery=self._chk_gallery.isEnabled() and self._chk_gallery.isChecked(),
            title=self._title,
            theme=theme_dict(self.config),
        )
        try:
            func(self._tree, opts, path)
        except Exception as e:
            themed_info(self.config, self, "Ошибка экспорта",
                        f"Не удалось выполнить экспорт:\n{e}")
            return
        themed_info(self.config, self, "Готово",
                    f"Экспортировано в файл:\n{path}")
        self.accept()
