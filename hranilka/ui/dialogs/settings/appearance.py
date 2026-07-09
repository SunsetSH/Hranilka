"""Вкладка «Внешний вид»: шрифт, кегль, тема, цвета, живой предпросмотр.
Часть SettingsDialog (dialog.py) — методы вынесены дословно (backlog-разрез по страницам)."""
from PySide6.QtWidgets import (QVBoxLayout, QGroupBox, QFormLayout, QComboBox, QPushButton, QLabel, QCheckBox, QWidget)
from PySide6.QtGui import QFontDatabase
from hranilka.core.config import RETRO_THEMES


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
        lay.addWidget(theme_group)

        colors_group = QGroupBox("Назначить цвета темы вручную")
        cl = QFormLayout(colors_group)
        self.text_color_btn  = QPushButton()
        self.tree_bg_btn     = QPushButton()
        self.main_bg_btn     = QPushButton()
        self.text_color_btn.clicked.connect(lambda: self._choose_color("text_color"))
        self.tree_bg_btn.clicked.connect(lambda: self._choose_color("tree_bg_color"))
        self.main_bg_btn.clicked.connect(lambda: self._choose_color("main_bg_color"))
        cl.addRow("Цвет текста:", self.text_color_btn)
        cl.addRow("Фон дерева:", self.tree_bg_btn)
        cl.addRow("Основной фон:", self.main_bg_btn)
        self._refresh_color_btns()
        lay.addWidget(colors_group)

        geo_group = QGroupBox("Окно")
        gl = QVBoxLayout(geo_group)
        self.remember_geo_check = QCheckBox("Запоминать размер и положение окна")
        self.remember_geo_check.setChecked(self.config.get("remember_geometry", False))
        gl.addWidget(self.remember_geo_check)
        lay.addWidget(geo_group)

        lay.addStretch()
        return w

