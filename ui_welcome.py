"""Приветственное окно (обучение) при первом запуске.

WelcomeDialog — слайды с обзором основных функций на живых мини-виджетах:
элементы собираются прямо в диалоге и темизируются текущей темой, скриншоты
не нужны. Все мини-виджеты декоративные: без БД, без буфера обмена, без
записи конфига. Показ при первом запуске решает обвязка в main.py
(should_show + флаг welcome_shown); из настроек диалог открывается повторно
без участия флага.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QStackedWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import config as config_mod
from theme import ThemedDialog


def should_show(config) -> bool:
    """True, если обучение ещё не показывали (первый запуск)."""
    return not config.get("welcome_shown", False)


class WelcomeDialog(ThemedDialog):
    """Слайдовое обучение: заголовок + короткий текст + живой мини-виджет."""

    def __init__(self, config, parent=None):
        super().__init__(config, parent)
        self.setWindowTitle("Обучение")
        self.setFixedSize(660, 520)

        self._stack = QStackedWidget()
        for build in (self._slide_welcome, self._slide_tree, self._slide_card,
                      self._slide_security, self._slide_tools, self._slide_final):
            self._stack.addWidget(build())
        self.body.addWidget(self._stack, 1)

        self._dots = QLabel()
        self._dots.setAlignment(Qt.AlignCenter)
        self.body.addWidget(self._dots)

        nav = QHBoxLayout()
        self._skip_btn = QPushButton("Пропустить")
        self._skip_btn.clicked.connect(self.reject)
        nav.addWidget(self._skip_btn)
        nav.addStretch()
        self._back_btn = QPushButton("< Назад")
        self._back_btn.clicked.connect(lambda: self._go(-1))
        nav.addWidget(self._back_btn)
        self._next_btn = QPushButton("Далее >")
        self._next_btn.clicked.connect(self._on_next)
        self._next_btn.setDefault(True)
        nav.addWidget(self._next_btn)
        self.body.addLayout(nav)

        self._sync_nav()

    # ----- Навигация -----

    def _go(self, delta: int):
        index = self._stack.currentIndex() + delta
        self._stack.setCurrentIndex(max(0, min(index, self._stack.count() - 1)))
        self._sync_nav()

    def _on_next(self):
        if self._stack.currentIndex() == self._stack.count() - 1:
            self.accept()
        else:
            self._go(+1)

    def _sync_nav(self):
        index, count = self._stack.currentIndex(), self._stack.count()
        last = index == count - 1
        self._back_btn.setEnabled(index > 0)
        self._next_btn.setText("Начать работу" if last else "Далее >")
        self._skip_btn.setVisible(not last)
        self._dots.setText(" ".join("●" if i == index else "○" for i in range(count)))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Right:
            self._go(+1)
        elif e.key() == Qt.Key_Left:
            self._go(-1)
        else:
            super().keyPressEvent(e)

    # ----- Каркас слайда -----

    def _slide(self, title: str, text: str):
        """Общий каркас: заголовок + пояснение; билдер добавляет свой виджет."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("appTitle")
        heading.setWordWrap(True)
        lay.addWidget(heading)
        body = QLabel(text)
        body.setWordWrap(True)
        lay.addWidget(body)
        return page, lay

    def _theme_colors(self):
        c = self.config
        return (c.get("font", "Cascadia Code"), c.get("font_size", 14),
                c.get("text_color", "#000000"), c.get("tree_bg_color", "#FFFFFF"),
                c.get("main_bg_color", "#F0F0F0"))

    # ----- Слайды -----

    def _slide_welcome(self):
        page, lay = self._slide(
            "Добро пожаловать в Хранилку",
            "Хранилка — оффлайн-хаб учётных записей. Все данные лежат в одном "
            "файле на вашем компьютере — никаких облаков и интернета.\n\n"
            "Это короткое обучение можно закрыть в любой момент (крестик, Esc "
            "или «Пропустить») и открыть позже: Настройки → Поведение.",
        )
        lay.addStretch()
        title = QLabel("ХРАНИЛКА")
        title.setObjectName("appTitle")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        strip = QLabel("локально · офлайн · ретро")
        strip.setAlignment(Qt.AlignCenter)
        lay.addWidget(strip)
        lay.addStretch()
        return page

    def _slide_tree(self):
        page, lay = self._slide(
            "Дерево: папки → сервисы → аккаунты",
            "Слева — дерево. Папка группирует сервисы, сервис — аккаунты. "
            "Создавайте узлы через правый клик или кнопки над деревом. "
            "Ищите по имени, отмечайте избранное (*), меняйте порядок "
            "перетаскиванием.",
        )
        font_name, font_size, text_color, tree_bg, main_bg = self._theme_colors()
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setSelectionMode(QTreeWidget.NoSelection)
        tree.setFocusPolicy(Qt.NoFocus)
        # dialog_stylesheet не покрывает QTreeWidget — локальный стиль по
        # образцу дерева главного окна.
        tree.setStyleSheet(
            f"QTreeWidget {{ border: 2px inset #808080; background-color: {tree_bg}; "
            f"color: {text_color}; font-family: '{font_name}'; font-size: {font_size}px; }}"
            f"QTreeWidget::item {{ padding: 4px; }}"
        )
        folder = QTreeWidgetItem(tree, ["[+] Почта"])
        service = QTreeWidgetItem(folder, ["[o] Gmail"])
        QTreeWidgetItem(service, ["* (i) личный@gmail.com"])
        tree.expandAll()
        tree.setFixedHeight(140)
        lay.addWidget(tree)
        lay.addStretch()
        return page

    def _slide_card(self):
        page, lay = self._slide(
            "Карточка аккаунта",
            "Справа — карточка: логин и пароль, секретные вопросы, коды, "
            "личные данные, галерея изображений и связи между аккаунтами. "
            "Кнопки [КОП] копируют поле в буфер обмена.\n\n"
            "Несохранённые правки не теряются при переключении, но помечаются "
            "в дереве — не забывайте нажимать «Сохранить».",
        )
        row = QHBoxLayout()
        field = QLineEdit("hunter2")
        field.setReadOnly(True)
        field.setEchoMode(QLineEdit.Password)
        row.addWidget(field)
        reveal = QPushButton("[*]")
        reveal.setFixedWidth(46)
        reveal.setToolTip("Показать/скрыть")
        reveal.clicked.connect(lambda: field.setEchoMode(
            QLineEdit.Normal if field.echoMode() == QLineEdit.Password
            else QLineEdit.Password))
        row.addWidget(reveal)
        copy = QPushButton("[КОП]")
        copy.setEnabled(False)
        copy.setToolTip("Пример — копирование работает в настоящей карточке")
        row.addWidget(copy)
        lay.addLayout(row)
        marker = QLabel("● НЕ СОХРАНЕНО ▸ (i) личный@gmail.com")
        marker_font = marker.font()
        marker_font.setBold(True)
        marker.setFont(marker_font)
        lay.addWidget(marker)
        lay.addStretch()
        return page

    def _slide_security(self):
        page, lay = self._slide(
            "Защитите свои данные",
            "ВАЖНО: по умолчанию шифрование ВЫКЛЮЧЕНО — файл базы прочитает "
            "любой, кто до него доберётся. Для настоящих секретов включите "
            "мастер-пароль: Настройки → Безопасность.\n\n"
            "Там же: код восстановления (показывается один раз!), "
            "автоблокировка, автоочистка буфера обмена и защита от скриншотов.",
        )
        _, _, text_color, tree_bg, _ = self._theme_colors()
        panel = QFrame()
        panel.setStyleSheet(
            f"QFrame {{ border: 2px inset #808080; background-color: {tree_bg}; }}"
            f"QLabel {{ border: none; color: {text_color}; font-weight: bold; }}"
        )
        panel_lay = QHBoxLayout(panel)
        panel_lay.addWidget(QLabel("[!!!]"))
        warn = QLabel("ШИФРОВАНИЕ: ВЫКЛЮЧЕНО → включите в Настройках")
        warn.setWordWrap(True)
        panel_lay.addWidget(warn, 1)
        lay.addWidget(panel)
        lay.addStretch()
        return page

    def _slide_tools(self):
        page, lay = self._slide(
            "Инструменты",
            "Встроенный генератор создаёт стойкие пароли и тестовые личные "
            "данные (РУ/США). Метка [!] в дереве напоминает сменить "
            "устаревший пароль. Экспорт — в TXT/CSV/XLSX/HTML; удалённое "
            "попадает в корзину (если включена).",
        )
        row = QHBoxLayout()
        sample = QLineEdit("Kq7#vR2$wLx9")
        sample.setReadOnly(True)
        row.addWidget(sample)
        gen = QPushButton("Сгенерировать")
        gen.setEnabled(False)
        gen.setToolTip("Пример — генератор работает в настоящей карточке")
        row.addWidget(gen)
        lay.addLayout(row)
        lay.addWidget(QLabel("(i) личный@gmail.com  [!]  ← пора менять пароль"))
        lay.addStretch()
        return page

    def _slide_final(self):
        page, lay = self._slide(
            "Настройте под себя",
            "8 ретро-тем, свой шрифт и цвета — Настройки → Внешний вид. "
            "Горячие клавиши перечислены в Настройки → Шорткаты.\n\n"
            "Готово: создайте первую папку — и вперёд.",
        )
        _, _, text_color, _, _ = self._theme_colors()
        swatches = QHBoxLayout()
        for theme_name, colors in config_mod.RETRO_THEMES.items():
            square = QFrame()
            square.setFixedSize(18, 18)
            square.setToolTip(theme_name)
            square.setStyleSheet(f"background-color: {colors['main_bg']}; "
                                 f"border: 1px solid {text_color};")
            swatches.addWidget(square)
        swatches.addStretch()
        lay.addLayout(swatches)
        current = self.config.get("selected_theme", "")
        lay.addWidget(QLabel(f"Текущая тема: {current}"))
        lay.addStretch()
        return page
