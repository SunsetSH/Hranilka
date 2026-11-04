"""Вкладка «Внешний вид»: шрифт, кегль, тема, цвета. Изменения сразу видны в
окнах программы; блок ThemePreview временно скрыт (SHOW_THEME_PREVIEW).
Часть SettingsDialog (dialog.py) — методы вынесены дословно (backlog-разрез по страницам)."""
import logging
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QComboBox, QPushButton, QLabel,
                               QCheckBox, QWidget, QFileDialog)
from PySide6.QtGui import QFontDatabase
from hranilka.core import theme_io
from hranilka.core.config import RETRO_THEMES
from hranilka.ui.retro_style import CHROME_LABELS, CHROME_STYLES, normalize_chrome
from hranilka.ui.theme import dialog_stylesheet, themed_info
from hranilka.ui.dialogs.settings.theme_preview import ThemePreview

logger = logging.getLogger(__name__)

# Блок «Предпросмотр» временно скрыт: изменения и так сразу видны в окнах
# программы, задумка требует доработки. Код ThemePreview сохранён — при False
# виджет не создаётся и место под него не резервируется.
SHOW_THEME_PREVIEW = False

# Ключ цвета в config -> ключ в RETRO_THEMES (для «Сбросить к пресету»).
_COLOR_PRESET_KEY = {"text_color": "text", "tree_bg_color": "tree_bg",
                     "main_bg_color": "main_bg"}


# Курируемый набор «программистских» моноширинных шрифтов в духе 2000-х.
# Cascadia Code — по умолчанию, удалять нельзя. Показываются только те, что
# реально установлены в системе (плюс текущий выбранный — на всякий случай).
CODING_FONTS = [
    "Cascadia Code", "Consolas", "Courier New",
    "Lucida Console", "Lucida Sans Typewriter", "Fixedsys",
    "Source Code Pro", "JetBrains Mono", "Fira Code", "DejaVu Sans Mono",
    "Liberation Mono",
]


class SettingsAppearanceMixin:
    # ─── Вкладка: Внешний вид ────────────────────────────────────────────────

    def _page_appearance(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.setContentsMargins(0, 8, 0, 0)

        font_group = QGroupBox("Шрифт интерфейса")
        ff = QFormLayout(font_group)

        self.font_combo = QComboBox()
        available = set(QFontDatabase.families())
        fonts = [f for f in CODING_FONTS if f in available]
        if "Cascadia Code" not in fonts:        # всегда доступен, не удаляем
            fonts.insert(0, "Cascadia Code")
        saved_font = self.config.get("font", "Cascadia Code")
        if saved_font not in fonts:             # показать текущий, даже если не из набора
            fonts.append(saved_font)
        self.font_combo.addItems(fonts)
        self.font_combo.setCurrentText(saved_font)
        self.font_combo.currentTextChanged.connect(self._apply_font_preview)

        self.font_size_combo = QComboBox()
        for size in range(8, 25):
            self.font_size_combo.addItem(str(size))
        self.font_size_combo.setCurrentText(str(self.config.get("font_size", 14)))
        self.font_size_combo.currentTextChanged.connect(self._apply_font_preview)

        ff.addRow("Шрифт:", self.font_combo)
        ff.addRow("Размер (кегль):", self.font_size_combo)
        ff.addRow("", QLabel("оптимально 13–16"))
        lay.addWidget(font_group)

        columns = QHBoxLayout()
        left = QVBoxLayout()
        columns.addLayout(left, 3)
        theme_group = QGroupBox("Готовые темы")
        tl = QVBoxLayout(theme_group)
        self.theme_combo = QComboBox()
        for name in RETRO_THEMES:
            self.theme_combo.addItem(name)
        # Восстанавливаем сохранённую тему до подключения сигнала, чтобы не
        # перезаписать текущие цвета при открытии диалога.
        saved_theme = self.config.get("selected_theme", "")
        idx = self.theme_combo.findText(saved_theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.currentTextChanged.connect(self._apply_theme)
        tl.addWidget(QLabel("Выберите тему:"))
        tl.addWidget(self.theme_combo)
        io_row = QHBoxLayout()
        self.theme_export_btn = QPushButton("Экспорт темы…")
        self.theme_import_btn = QPushButton("Импорт темы…")
        self.theme_export_btn.clicked.connect(self._export_theme)
        self.theme_import_btn.clicked.connect(self._import_theme)
        io_row.addWidget(self.theme_export_btn)
        io_row.addWidget(self.theme_import_btn)
        tl.addLayout(io_row)
        left.addWidget(theme_group)

        colors_group = QGroupBox("Назначить цвета темы вручную")
        cl = QFormLayout(colors_group)
        self.text_color_btn  = QPushButton()
        self.tree_bg_btn     = QPushButton()
        self.main_bg_btn     = QPushButton()
        self.text_color_btn.clicked.connect(lambda: self._choose_color("text_color"))
        self.tree_bg_btn.clicked.connect(lambda: self._choose_color("tree_bg_color"))
        self.main_bg_btn.clicked.connect(lambda: self._choose_color("main_bg_color"))
        self._reset_color_btns = {}
        for label, key, btn in (("Цвет текста:", "text_color", self.text_color_btn),
                                ("Фон дерева:", "tree_bg_color", self.tree_bg_btn),
                                ("Основной фон:", "main_bg_color", self.main_bg_btn)):
            reset = QPushButton("Сброс")
            reset.setToolTip("Сбросить к цвету выбранной готовой темы")
            reset.clicked.connect(lambda _=False, k=key: self._reset_color(k))
            self._reset_color_btns[key] = reset
            row = QHBoxLayout()
            row.addWidget(btn, 1)
            row.addWidget(reset)
            cl.addRow(label, row)
        left.addWidget(colors_group)

        chrome_group = QGroupBox("Оформление окна")
        chl = QFormLayout(chrome_group)
        self.chrome_style_combo = QComboBox()
        for key in CHROME_STYLES:
            self.chrome_style_combo.addItem(CHROME_LABELS[key], key)
        cur = self.chrome_style_combo.findData(
            normalize_chrome(self.config.get("chrome_style", "flat")))
        self.chrome_style_combo.setCurrentIndex(max(cur, 0))
        self.chrome_style_combo.currentIndexChanged.connect(self._apply_chrome_preview)
        self.crt_mode_check = QCheckBox("CRT-эффект (линии развёртки и виньетка)")
        self.crt_mode_check.setChecked(bool(self.config.get("crt_mode", False)))
        self.crt_mode_check.toggled.connect(self._apply_chrome_preview)
        chl.addRow("Стиль окна:", self.chrome_style_combo)
        # Однострочный addRow: на всю ширину формы, иначе пустая колонка подписи
        # съедает место и текст чекбокса обрезается.
        chl.addRow(self.crt_mode_check)
        left.addWidget(chrome_group)
        left.addStretch()

        if SHOW_THEME_PREVIEW:
            preview_group = QGroupBox("Предпросмотр")
            pvl = QVBoxLayout(preview_group)
            self.theme_preview = ThemePreview(self._theme_preview_values())
            self.theme_preview.setMinimumWidth(260)
            pvl.addWidget(self.theme_preview)
            pvl.addStretch()
            columns.addWidget(preview_group, 2)
        lay.addLayout(columns)
        self._refresh_color_btns()

        icons_group = QGroupBox("Дерево")
        igl = QFormLayout(icons_group)
        self.tree_icons_combo = QComboBox()
        self.tree_icons_combo.addItem("текстовые", "text")
        self.tree_icons_combo.addItem("пиксельные", "pixel")
        cur_icons = self.tree_icons_combo.findData(
            self.config.get("tree_icons", "text"))
        self.tree_icons_combo.setCurrentIndex(max(cur_icons, 0))
        self.tree_icons_combo.currentIndexChanged.connect(self._apply_tree_icons_preview)
        igl.addRow("Иконки дерева:", self.tree_icons_combo)
        lay.addWidget(icons_group)

        cards_group = QGroupBox("Карточки")
        cgl = QVBoxLayout(cards_group)
        self.hide_empty_card_fields_check = QCheckBox(
            "Скрывать пустые поля и вкладки в режиме просмотра")
        self.hide_empty_card_fields_check.setChecked(
            self.config.get("hide_empty_card_fields", True))
        cgl.addWidget(self.hide_empty_card_fields_check)
        cards_note = QLabel(
            "В режиме редактирования все поля и вкладки остаются доступными.")
        cards_note.setWordWrap(True)
        cgl.addWidget(cards_note)
        lay.addWidget(cards_group)

        geo_group = QGroupBox("Окно")
        gl = QVBoxLayout(geo_group)
        self.remember_geo_check = QCheckBox("Запоминать размер и положение окна")
        self.remember_geo_check.setChecked(self.config.get("remember_geometry", False))
        gl.addWidget(self.remember_geo_check)
        lay.addWidget(geo_group)

        lay.addStretch()
        return w

    def _chrome_style_value(self) -> str:
        return normalize_chrome(self.chrome_style_combo.currentData())

    def _tree_icons_value(self) -> str:
        return "pixel" if self.tree_icons_combo.currentData() == "pixel" else "text"

    def _apply_tree_icons_preview(self, *_):
        """Живой предпросмотр режима иконок дерева."""
        self.config.set("tree_icons", self._tree_icons_value())
        self.appearance_changed.emit()

    def _apply_chrome_preview(self, *_):
        """Живой предпросмотр стиля окна/CRT в главном окне (только стили)."""
        self.config.set("chrome_style", self._chrome_style_value())
        self.config.set("crt_mode", self.crt_mode_check.isChecked())
        self.setStyleSheet(dialog_stylesheet(self.config))
        self._schedule_preview()
        self.appearance_changed.emit()

    # ─── Превью, сброс к пресету, экспорт/импорт темы (7.7) ──────────────────

    def _theme_preview_values(self) -> dict:
        return {
            "font": self.font_combo.currentText(),
            "font_size": int(self.font_size_combo.currentText() or "14"),
            "text_color": self.config.get("text_color"),
            "tree_bg_color": self.config.get("tree_bg_color"),
            "main_bg_color": self.config.get("main_bg_color"),
            "chrome_style": self._chrome_style_value(),
        }

    def _schedule_preview(self) -> None:
        if hasattr(self, "theme_preview"):
            self.theme_preview.schedule(self._theme_preview_values())

    def _preset_color(self, key: str):
        preset = RETRO_THEMES.get(self.theme_combo.currentText())
        return preset[_COLOR_PRESET_KEY[key]] if preset else None

    def _refresh_reset_btns(self) -> None:
        for key, btn in self._reset_color_btns.items():
            preset = self._preset_color(key)
            btn.setEnabled(preset is not None
                           and str(self.config.get(key, "")).upper() != preset.upper())

    def _reset_color(self, key: str) -> None:
        preset = self._preset_color(key)
        if preset is None:
            return
        self.config.set(key, preset)
        self._refresh_color_btns()
        self._restyle_shortcuts()
        self.setStyleSheet(dialog_stylesheet(self.config))
        self.appearance_changed.emit()

    def _theme_export_values(self) -> dict:
        v = self._theme_preview_values()
        v["selected_theme"] = self.theme_combo.currentText()
        v["crt_mode"] = self.crt_mode_check.isChecked()
        if hasattr(self, "sounds_enabled_check"):
            from hranilka.ui import sounds
            v["sounds_enabled"] = self.sounds_enabled_check.isChecked()
            v["sound_volume"] = self.sound_volume_slider.value()
            for event, box in self.sound_event_checks.items():
                v[sounds.config_key(event)] = box.isChecked()
        icons = self.config.get("tree_icons")
        if icons is not None:
            v["tree_icons"] = icons
        return v

    def _export_theme(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт темы", "hranilka-theme.json", "Тема Хранилки (*.json)")
        if not path:
            return
        try:
            theme_io.export_file(path, self._theme_export_values())
        except OSError as e:
            logger.error("Экспорт темы: %s", e)
            themed_info(self.config, self, "Ошибка",
                        f"Не удалось сохранить тему:\n{e.strerror or e}")
            return
        themed_info(self.config, self, "Экспорт темы", "Тема сохранена.")

    def _import_theme(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт темы", "", "Тема Хранилки (*.json)")
        if not path:
            return
        try:
            data = theme_io.import_file(path)
        except theme_io.ThemeIOError as e:
            themed_info(self.config, self, "Ошибка импорта", str(e))
            return
        self._apply_theme_values(data.values)
        if data.skipped:
            themed_info(self.config, self, "Импорт темы",
                        "Пропущены некорректные параметры: " + ", ".join(data.skipped))

    def _apply_theme_values(self, v: dict) -> None:
        """Переносит значения темы в контролы страницы (без записи на диск);
        откат — через reject(), как для обычного живого предпросмотра."""
        def quiet(widget, fn):
            blocked = widget.blockSignals(True)
            try:
                fn()
            finally:
                widget.blockSignals(blocked)

        if "font" in v:
            if self.font_combo.findText(v["font"]) < 0:
                self.font_combo.addItem(v["font"])
            quiet(self.font_combo, lambda: self.font_combo.setCurrentText(v["font"]))
        if "font_size" in v:
            quiet(self.font_size_combo,
                  lambda: self.font_size_combo.setCurrentText(str(v["font_size"])))
        if "selected_theme" in v:
            i = self.theme_combo.findText(v["selected_theme"])
            if i >= 0:
                quiet(self.theme_combo, lambda: self.theme_combo.setCurrentIndex(i))
        for key in _COLOR_PRESET_KEY:
            if key in v:
                self.config.set(key, v[key])
        if "chrome_style" in v:
            i = self.chrome_style_combo.findData(v["chrome_style"])
            if i >= 0:
                quiet(self.chrome_style_combo,
                      lambda: self.chrome_style_combo.setCurrentIndex(i))
        if "crt_mode" in v:
            quiet(self.crt_mode_check, lambda: self.crt_mode_check.setChecked(v["crt_mode"]))
        self._apply_imported_sounds(v)
        if "tree_icons" in v:
            i = self.tree_icons_combo.findData(v["tree_icons"])
            if i >= 0:
                self.tree_icons_combo.setCurrentIndex(i)
        self._apply_font_preview()
        self._apply_chrome_preview()
        self._refresh_color_btns()

    def _apply_imported_sounds(self, v: dict) -> None:
        if not hasattr(self, "sounds_enabled_check"):
            return
        from hranilka.ui import sounds
        if "sounds_enabled" in v:
            self.sounds_enabled_check.setChecked(v["sounds_enabled"])
        if "sound_volume" in v:
            self.sound_volume_slider.setValue(v["sound_volume"])
        for event, box in self.sound_event_checks.items():
            if sounds.config_key(event) in v:
                box.setChecked(v[sounds.config_key(event)])
