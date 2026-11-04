"""Живое мини-превью темы для страницы «Внешний вид» (7.7).

Рисуется собственным QSS/RetroStyle на основе временного словаря значений,
НЕ трогает конфиг и не перерисовывает остальное приложение."""
from typing import Any, Mapping, Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from hranilka.ui import theme
from hranilka.ui.retro_style import (DEFAULT_CHROME, RetroStyle, chrome_theme,
                                     normalize_chrome, retro_metrics)
from hranilka.ui.titlebar import TitleBar

DEBOUNCE_MS = 80

_DEFAULTS: dict[str, Any] = {
    "font": "Cascadia Code", "font_size": 14, "text_color": "#000000",
    "tree_bg_color": "#FFFFFF", "main_bg_color": "#F0F0F0",
    "chrome_style": DEFAULT_CHROME,
}


class _DictConfig:
    """Config-подобный объект поверх словаря (только .get)."""

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._d = dict(values)

    def get(self, key: str, default: Any = None) -> Any:
        return self._d.get(key, default)


class ThemePreview(QFrame):
    def __init__(self, values: Optional[Mapping[str, Any]] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("themePreview")
        self._pending: dict[str, Any] = dict(_DEFAULTS)
        self._style = RetroStyle("Fusion")      # владеем стилем: setStyle его не забирает
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._render)

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(0)

        self.title_bar = TitleBar(self, self)
        self.title_bar.settings_btn.hide()
        self.title_bar.title.setText("ХРАНИЛКА — превью")
        root.addWidget(self.title_bar)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(8, 8, 8, 8)
        bl.setSpacing(6)
        self.sample_label = QLabel("Образец текста")
        bl.addWidget(self.sample_label)
        row = QHBoxLayout()
        self.button = QPushButton("Кнопка")
        self.line_edit = QLineEdit("Поле ввода")
        row.addWidget(self.button)
        row.addWidget(self.line_edit, 1)
        bl.addLayout(row)
        self.list_widget = QListWidget()
        self.list_widget.addItems(["Папка", "Аккаунт", "Сервер"])
        self.list_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        bl.addWidget(self.list_widget)
        self.status_label = QLabel("Готово")
        self.status_label.setProperty("statusPanel", True)
        bl.addWidget(self.status_label)
        root.addWidget(body)

        self.set_values(values or {})

    # ─── API ────────────────────────────────────────────────────────────────

    def set_values(self, values: Mapping[str, Any]) -> None:
        """Немедленное обновление."""
        self._merge(values)
        self._timer.stop()
        self._render()

    def schedule(self, values: Mapping[str, Any]) -> None:
        """Обновление с дебаунсом (частые изменения контролов)."""
        self._merge(values)
        self._timer.start()

    # ─── Внутреннее ─────────────────────────────────────────────────────────

    def _merge(self, values: Mapping[str, Any]) -> None:
        self._pending.update({k: v for k, v in values.items() if v is not None})

    def _render(self) -> None:
        cfg = _DictConfig(self._pending)
        cfg._d["chrome_style"] = normalize_chrome(cfg.get("chrome_style"))
        th = chrome_theme(cfg)
        self._style.configure(th)
        _, size, text, tree_bg, main_bg = theme._colors(cfg)
        p = theme.palette(cfg)
        self.setStyleSheet(
            f"#themePreview {{ background-color: {main_bg}; border: 2px solid {p.border}; }}"
            + theme.widget_styles(cfg) + theme.header_styles(cfg))
        for w in [self, *self.findChildren(QWidget)]:
            w.setStyle(self._style)
        f = QFont(str(cfg.get("font")))
        f.setPixelSize(int(size) + 5)
        f.setBold(True)
        self.title_bar.apply_theme(th, retro_metrics(f))
        self.list_widget.setFixedHeight(
            self.list_widget.sizeHintForRow(0) * 3 + 2 * self.list_widget.frameWidth() + 16)
        self.update()
