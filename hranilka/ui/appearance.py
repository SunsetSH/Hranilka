"""Внешний вид главного окна (AppearanceMixin): шрифт, QSS окна и дерева,
стрелки ветвей дерева. Легковесно и идемпотентно — подходит для живого
предпросмотра настроек (debounce + пропуск неизменившегося QSS)."""
import atexit
import os
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

from hranilka.ui import theme, retro_style
from hranilka.ui.crt_overlay import set_crt


def tree_stylesheet(font_name, font_size, text_color, tree_bg, main_bg, border, arrow_css="",
                    bevel=False):
    """QSS дерева аккаунтов. Подсветка строки при наведении выводится из цветов
    темы (theme.hover_color), чтобы быть видимой и при tree_bg == main_bg."""
    hover = theme.hover_color(tree_bg, main_bg, text_color)
    frame = "padding: 2px;" if bevel else f"border: 2px inset {border};"   # bevel: рамку рисует RetroStyle
    return f"""
            QTreeWidget {{ {frame} background-color: {tree_bg}; color: {text_color}; font-family: '{font_name}'; font-size: {font_size}px; }}
            QTreeWidget::item {{ padding: 4px; border: 1px solid transparent; }}
            QTreeWidget::item:hover {{ background-color: {hover}; }}
            QTreeWidget::item:selected {{ background-color: {text_color}; color: {tree_bg}; }}
        """ + arrow_css


class AppearanceMixin:
    """Миксин MainWindow: apply_appearance и стрелки дерева."""

    APPEARANCE_DEBOUNCE_MS = 60

    # Состояние (атрибуты класса — без getattr-проверок):
    _appearance_sig: Optional[tuple] = None        # входные настройки последнего применения
    _applied_qss: Optional[tuple] = None           # (шрифт, QSS окна, QSS дерева)
    _appearance_timer: Optional[QTimer] = None
    _tree_icons_sig: Optional[tuple] = None        # (text, tree_bg, tree_icons)
    _branch_css_cache: Optional[tuple] = None      # ((text, tree_bg), css)
    _branch_icon_dir_path: Optional[str] = None

    def request_appearance(self) -> None:
        """Живой предпросмотр: схлопывает серию быстрых изменений в одно
        применение (debounce)."""
        if self._appearance_timer is None:
            self._appearance_timer = QTimer(self)
            self._appearance_timer.setSingleShot(True)
            self._appearance_timer.timeout.connect(self.apply_appearance)
        self._appearance_timer.start(self.APPEARANCE_DEBOUNCE_MS)

    def flush_appearance(self) -> None:
        """Применить отложенный предпросмотр немедленно (закрытие диалога)."""
        t = self._appearance_timer
        if t is not None and t.isActive():
            t.stop()
            self.apply_appearance()

    def apply_appearance(self):
        """Только визуальная часть (шрифт, стили). Лёгкая — подходит для живого
        предпросмотра настроек, не трогает геометрию/таймеры/БД.

        Повторное применение с теми же значениями пропускаем, а из применяемых
        частей перекрашиваем только изменившиеся: setStyleSheet на главном окне
        вызывает полный repolish дерева (сотни элементов)."""
        font_name = self.config.get("font", "Cascadia Code")
        font_size = self.config.get("font_size", 14)
        text_color = self.config.get("text_color", "#FFFFFF")
        tree_bg = self.config.get("tree_bg_color", "#0000AA")
        main_bg = self.config.get("main_bg_color", "#0000AA")

        chrome = retro_style.normalize_chrome(self.config.get("chrome_style", "flat"))
        crt = bool(self.config.get("crt_mode", False))
        tree_icons = self.config.get("tree_icons", "text")
        signature = (font_name, font_size, text_color, tree_bg, main_bg, chrome, crt,
                     tree_icons)
        icons_sig = (text_color, tree_bg, tree_icons)
        if self._appearance_sig == signature:
            return                              # внешний вид не менялся — repolish не нужен
        self._appearance_sig = signature

        retro_style.configure_app(self.config)      # фаски — до QSS (repolish)
        main_qss = theme.main_stylesheet(self.config)
        tree_qss = tree_stylesheet(
            font_name, font_size, text_color, tree_bg, main_bg,
            theme.palette(self.config).border,
            self._branch_arrow_css(text_color, tree_bg, main_bg),
            bevel=chrome != "flat")
        prev_font, prev_main, prev_tree = self._applied_qss or (None, None, None)
        if (font_name, font_size) != prev_font:
            self.setFont(QFont(font_name, font_size))
        if main_qss != prev_main:
            # Общий стиль окна (вкл. кнопки-вкладки, спинбоксы, комбобоксы)
            self.setStyleSheet(main_qss)
            self.tabs.apply_scroll_bg(main_bg)
            for fin_tabs in self.fin_tabs_by_type.values():
                fin_tabs.apply_scroll_bg(main_bg)
            self.server_tabs.apply_scroll_bg(main_bg)
        if tree_qss != prev_tree:
            self.tree.setStyleSheet(tree_qss)
        self.tree.set_drop_indicator_color(text_color)
        if icons_sig != self._tree_icons_sig:     # режим/цвета иконок дерева (7.5)
            self._tree_icons_sig = icons_sig
            self.refresh_tree_icons()
        title_bar = getattr(self, "title_bar", None)
        if title_bar is not None:
            title_bar.set_chrome(self.config)
        set_crt(self, crt)
        self._applied_qss = ((font_name, font_size), main_qss, tree_qss)
        self._sync_tree_buttons()
        self._update_favorites_btn()
        self._update_tree_toggle_btn()

    def _branch_arrow_css(self, text_color, tree_bg, main_bg):
        """Стрелки сворачивания/разворачивания, перекрашенные под тему.

        Стандартные стрелки рисуются стилем ОС фиксированным цветом и теряются
        на выделении (фон строки = text_color). Генерируем свои треугольники:
        в обычном состоянии — цветом текста, на выделении — цветом фона дерева
        (как инвертируется текст), чтобы стрелка всегда оставалась видимой.

        QSS в этой версии Qt не рендерит image:url() с data:-URI (проверено
        офскрин-рендером — картинка тихо не показывается, стрелки пропадают).
        Поэтому пишем PNG-файлы во временный каталог, но только когда цвета
        реально меняются (кэш по паре text_color/tree_bg, M-17) — не на каждый
        apply_appearance. Каталог создаётся один раз за запуск и удаляется
        через atexit."""
        cache_key = (text_color, tree_bg)
        cached = self._branch_css_cache
        if (cached is not None and cached[0] == cache_key
                and self._branch_icon_dir_path
                and os.path.isdir(self._branch_icon_dir_path)):
            body = cached[1]
        else:
            body = self._render_branch_arrow_files(text_color, tree_bg)
            self._branch_css_cache = (cache_key, body)
        # Заливка области стрелки (background-color) зависит от main_bg (hover),
        # поэтому её оставляем вне кэша по цветам стрелок — она дешёвая (текст).
        return f"""
            QTreeWidget::branch {{ background-color: {tree_bg}; }}
            QTreeWidget::branch:hover {{ background-color: {theme.hover_color(tree_bg, main_bg, text_color)}; }}
            QTreeWidget::branch:selected {{ background-color: {text_color}; }}
        """ + body

    def _branch_icon_dir(self):
        """Временный каталог для PNG стрелок дерева; создаётся один раз за
        запуск и удаляется целиком при выходе (atexit)."""
        d = self._branch_icon_dir_path
        if d is None or not os.path.isdir(d):
            import tempfile
            d = tempfile.mkdtemp(prefix="hranilka-arrows-")
            self._branch_icon_dir_path = d
            atexit.register(self._cleanup_branch_icon_dir)
        return d

    def _cleanup_branch_icon_dir(self):
        d = self._branch_icon_dir_path
        if d:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def _render_branch_arrow_files(self, text_color, tree_bg):
        """Отрисовать 4 стрелки в файлы (перезаписывая прежние для этих же
        имён) и вернуть QSS-правила с путями к ним."""
        from PySide6.QtGui import QPixmap, QPainter, QPolygon, QColor
        from PySide6.QtCore import QPoint

        icon_dir = self._branch_icon_dir()

        def make(direction, color, name):
            pm = QPixmap(16, 16)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(QColor(color))
            p.setPen(Qt.PenStyle.NoPen)
            if direction == "closed":      # ▶
                p.drawPolygon(QPolygon([QPoint(5, 3), QPoint(11, 8), QPoint(5, 13)]))
            else:                          # ▼
                p.drawPolygon(QPolygon([QPoint(3, 5), QPoint(13, 5), QPoint(8, 11)]))
            p.end()
            path = os.path.join(icon_dir, name)
            pm.save(path, "PNG")
            # QSS ожидает прямые слэши даже на Windows.
            return path.replace("\\", "/")

        cn = make("closed", text_color, "cn.png")
        op = make("open", text_color, "op.png")
        cs = make("closed", tree_bg, "cs.png")
        ops = make("open", tree_bg, "ops.png")
        return f"""
            QTreeWidget::branch:has-children:closed {{ image: url("{cn}"); }}
            QTreeWidget::branch:has-children:open {{ image: url("{op}"); }}
            QTreeWidget::branch:has-children:closed:selected {{ image: url("{cs}"); }}
            QTreeWidget::branch:has-children:open:selected {{ image: url("{ops}"); }}
        """
