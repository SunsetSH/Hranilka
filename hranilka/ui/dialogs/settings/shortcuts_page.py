"""Вкладка «Шорткаты»: назначение горячих клавиш (KeyCaptureDialog),
сброс к значениям по умолчанию.
Часть SettingsDialog (dialog.py) — методы вынесены дословно (backlog-разрез по страницам)."""
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton, QLabel, QWidget, QFrame, QDialog, QScrollArea)
from PySide6.QtCore import Qt
from hranilka.ui.theme import themed_confirm
from hranilka.ui.dialogs.key_capture import KeyCaptureDialog
from hranilka.core import shortcuts


class SettingsShortcutsMixin:
    # ─── Вкладка: Шорткаты ───────────────────────────────────────────────────

    def _page_shortcuts(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.addWidget(self._build_shortcuts_group(), 1)
        return w

    # ─── Секция: Шорткаты (горячие клавиши) ──────────────────────────────────

    def _build_shortcuts_group(self):
        # Рабочая копия сочетаний — правки копятся здесь, в конфиг попадают
        # только при «Применить и закрыть».
        self._sc_working = dict(shortcuts.effective(self.config))
        self._sc_badges = {}        # action_id → QPushButton-бейдж

        group = QGroupBox("Шорткаты (горячие клавиши)")
        outer_lay = QVBoxLayout(group)
        outer_lay.setSpacing(8)

        scroll = QScrollArea()
        self._sc_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # Горизонтальную прокрутку убираем — содержимое подгоняется по ширине.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # На собственной вкладке блок занимает всю доступную высоту; список
        # длинный — листается внутри прокрутки.
        scroll.setMinimumHeight(72)

        inner = QWidget()
        self._sc_inner = inner
        lay = QVBoxLayout(inner)
        lay.setSpacing(10)
        lay.setContentsMargins(0, 0, 0, 0)

        # Группировка по категориям в порядке shortcuts.CATEGORIES
        by_cat = {}
        for sid, label, _seq, cat in shortcuts.SHORTCUT_DEFS:
            by_cat.setdefault(cat, []).append((sid, label))

        for cat in shortcuts.CATEGORIES:
            if cat not in by_cat:
                continue
            cat_group = QGroupBox(cat)
            gl = QVBoxLayout(cat_group)
            for sid, label in by_cat[cat]:
                gl.addLayout(self._sc_row(sid, label))
            lay.addWidget(cat_group)

        lay.addStretch()
        scroll.setWidget(inner)
        outer_lay.addWidget(scroll, 1)

        # Кнопка общего сброса + предупреждение темой
        reset_all = QPushButton("Сбросить все к умолчанию")
        reset_all.clicked.connect(self._sc_reset_all)
        outer_lay.addWidget(reset_all)

        warn = QLabel(
            "Изменения вступают в силу после «Применить и закрыть». "
            "Сочетания Esc и Del работают в контексте дерева/режима редактирования.")
        warn.setWordWrap(True)
        self._sc_warn = warn
        outer_lay.addWidget(warn)

        self._restyle_shortcuts()       # инлайн-стили (фон/цвет) из текущей темы
        return group

    def _restyle_shortcuts(self):
        """Применяет к блоку шорткатов инлайн-стили, зависящие от темы (фон
        области прокрутки и цвет предупреждения). Вызывается при живом
        предпросмотре, чтобы блок менялся сразу, как остальные окна."""
        if not hasattr(self, "_sc_scroll"):
            return
        main_bg = self.config.get("main_bg_color", "#F0F0F0")
        text_color = self.config.get("text_color", "#000000")
        self._sc_scroll.setStyleSheet(
            f"QScrollArea {{ background: {main_bg}; border: none; }}")
        self._sc_scroll.viewport().setStyleSheet(f"background: {main_bg};")
        self._sc_inner.setStyleSheet("background: transparent;")
        self._sc_warn.setStyleSheet(f"color: {text_color}; font-weight: bold;")

    def _sc_row(self, sid, label):
        row = QHBoxLayout()
        row.addWidget(QLabel(label), 1)

        badge = QPushButton()
        badge.setEnabled(False)              # бейдж только показывает сочетание
        badge.setFixedWidth(150)             # одинаковая ширина для всех строк
        self._sc_badges[sid] = badge
        self._sc_update_badge(sid)
        row.addWidget(badge)

        change_btn = QPushButton("Изменить")
        change_btn.clicked.connect(lambda _=False, s=sid: self._sc_change(s))
        row.addWidget(change_btn)

        reset_btn = QPushButton("Сброс")
        reset_btn.clicked.connect(lambda _=False, s=sid: self._sc_reset_one(s))
        row.addWidget(reset_btn)
        return row

    def _sc_update_badge(self, sid):
        seq = self._sc_working.get(sid, "")
        self._sc_badges[sid].setText(seq if seq else "—")

    def _sc_change(self, sid):
        dlg = KeyCaptureDialog(self.config, self._sc_working, sid, self)
        if dlg.exec() == QDialog.Accepted:
            self._sc_working[sid] = dlg.result_sequence
            self._sc_update_badge(sid)

    def _sc_reset_one(self, sid):
        self._sc_working[sid] = shortcuts.DEFAULTS.get(sid, "")
        self._sc_update_badge(sid)

    def _sc_reset_all(self):
        if not themed_confirm(self.config, self, "Сброс шорткатов",
                              "Вернуть все сочетания к значениям по умолчанию?"):
            return
        self._sc_working = dict(shortcuts.DEFAULTS)
        for sid in self._sc_badges:
            self._sc_update_badge(sid)

