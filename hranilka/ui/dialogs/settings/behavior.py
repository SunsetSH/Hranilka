"""Вкладка «Поведение»: подтверждения, буфер обмена, корзина, галерея,
повторный показ обучения.
Часть SettingsDialog (dialog.py) — методы вынесены дословно (backlog-разрез по страницам)."""
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QComboBox, QPushButton, QLabel, QCheckBox, QLineEdit, QSizePolicy, QWidget)
from PySide6.QtGui import QIntValidator
from hranilka.ui.theme import themed_info
from hranilka.ui.welcome import WelcomeDialog

# Минимальная ширина числовых полей настроек (пикс.): чтобы на стилях без
# растяжения полей формы значение не сжималось и текст не обрезался.
_NUM_FIELD_MIN_W = 120


class SettingsBehaviorMixin:
    # ─── Вкладка: Поведение ──────────────────────────────────────────────────

    def _show_welcome(self):
        # Вложенный модальный диалог — норма кодовой базы (themed_info и т.п.).
        # Флаг welcome_shown здесь не трогаем: он касается только первого запуска.
        WelcomeDialog(
            self.config, self, self._apply_welcome_fin_instruments,
            self._apply_welcome_recycle_bin).exec()

    def _apply_welcome_fin_instruments(self, show: bool) -> bool:
        """Передать выбор учебного слайда главному окну и синхронизировать UI."""
        main_window = self.window()
        apply_choice = getattr(main_window, "_apply_welcome_fin_instruments", None)
        if apply_choice is None or not apply_choice(show):
            return False
        self.show_fin_check.setChecked(show)
        return True

    def _apply_welcome_recycle_bin(self, enabled: bool) -> bool:
        """Передать выбор корзины главному окну и синхронизировать вкладку."""
        main_window = self.window()
        apply_choice = getattr(main_window, "_apply_welcome_recycle_bin", None)
        if apply_choice is None or not apply_choice(enabled):
            return False
        self.recycle_bin_check.setChecked(enabled)
        return True

    def _page_behavior(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.setContentsMargins(0, 8, 0, 0)

        beh_group = QGroupBox("Поведение программы")
        bl = QVBoxLayout(beh_group)
        self.warn_exit_check = QCheckBox("Предупреждать о несохранённых данных при выходе")
        self.warn_exit_check.setChecked(self.config.get("warn_on_exit_unsaved", True))
        bl.addWidget(self.warn_exit_check)
        lay.addWidget(beh_group)

        clip_group = QGroupBox("Буфер обмена")
        cf = QFormLayout(clip_group)
        # Не полагаемся на стиль платформы: некоторые стили оставляют поля
        # формы размером sizeHint до следующего перерасчёта геометрии окна.
        cf.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.clip_clear_secs = QLineEdit(str(self.config.get("clipboard_clear_secs", 0)))
        self.clip_clear_secs.setValidator(QIntValidator(0, 3600, self))
        # Пол ширины: на стилях, где поле формы не растягивается (FieldsStayAtSizeHint),
        # числовое поле сжималось и текст обрезался. Выравниваем по прочим числовым
        # полям настроек (idle-минуты).
        self.clip_clear_secs.setMinimumWidth(_NUM_FIELD_MIN_W)
        # Высота — по sizeHint (Fixed), пересчитывается ThemedDialog.showEvent
        # после того, как система применит финальную геометрию окна (иначе на
        # некоторых мониторах поле оставалось сжато до перемещения окна).
        self.clip_clear_secs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        cf.addRow("Очистка буфера:", self.clip_clear_secs)
        cf.addRow("", QLabel("в секундах, 0 — не очищать"))
        self.clip_clear_exit_check = QCheckBox(
            "Очистка буфера при выходе из программы")
        self.clip_clear_exit_check.setChecked(
            self.config.get("clipboard_clear_on_exit", False))
        cf.addRow(self.clip_clear_exit_check)
        lay.addWidget(clip_group)

        img_group = QGroupBox("Изображения")
        il = QVBoxLayout(img_group)
        self.image_downscale_check = QCheckBox(
            "Сжимать большие изображения при добавлении")
        self.image_downscale_check.setChecked(self.config.get("image_downscale", True))
        il.addWidget(self.image_downscale_check)
        il.addWidget(QLabel(
            "Уменьшает очень большие картинки (длинная сторона > 2560px) до\n"
            "разумного размера в JPEG — меньше нагрузка и размер базы.\n"
            "Уже сохранённые изображения не затрагиваются."))
        lay.addWidget(img_group)

        gal_group = QGroupBox("Галерея")
        gf2 = QFormLayout(gal_group)
        self.thumb_preload_combo = QComboBox()
        # (config-значение, подпись) — индекс сохраняем по значению.
        self._THUMB_PRELOAD_OPTIONS = [
            ("startup",  "При запуске Хранилки"),
            ("on_click", "По нажатию на изображение"),
        ]
        for _, label in self._THUMB_PRELOAD_OPTIONS:
            self.thumb_preload_combo.addItem(label)
        self.thumb_preload_combo.setCurrentIndex(
            self._thumb_preload_index(self.config.get("gallery_thumb_preload", "startup")))
        gf2.addRow("Предзагрузка миниатюр в галерее:", self.thumb_preload_combo)
        # Однострочный addRow: подсказка занимает обе колонки формы (на всю
        # ширину окна), иначе текст начинается от комбобокса и обрезается.
        thumb_hint = QLabel(
            "«При запуске» подгружает все миниатюры сразу — быстрее просмотр, "
            "но весь объём изображений аккаунта держится в ОЗУ. «По нажатию» "
            "читает изображение только при клике — меньше нагрузка на память.")
        thumb_hint.setWordWrap(True)
        gf2.addRow(thumb_hint)
        lay.addWidget(gal_group)

        help_group = QGroupBox("Обучение")
        hl = QHBoxLayout(help_group)
        hl.addWidget(QLabel("Слайды с обзором основных функций программы"))
        hl.addStretch()
        self.show_welcome_btn = QPushButton("Показать обучение")
        self.show_welcome_btn.clicked.connect(self._show_welcome)
        hl.addWidget(self.show_welcome_btn)
        lay.addWidget(help_group)

        lay.addStretch()
        return w

    def _thumb_preload_index(self, value: str) -> int:
        for i, (key, _) in enumerate(self._THUMB_PRELOAD_OPTIONS):
            if key == value:
                return i
        return 0

    def _thumb_preload_value(self) -> str:
        return self._THUMB_PRELOAD_OPTIONS[self.thumb_preload_combo.currentIndex()][0]

