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
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from hranilka.core import config as config_mod
from hranilka.ui.theme import ThemedDialog


def should_show(config) -> bool:
    """True, если обучение ещё не показывали (первый запуск)."""
    return not config.get("welcome_shown", False)


class WelcomeDialog(ThemedDialog):
    """Слайдовое обучение: заголовок + короткий текст + живой мини-виджет."""

    def __init__(self, config, parent=None, on_fin_instruments_changed=None,
                 on_recycle_bin_changed=None, on_servers_changed=None):
        super().__init__(config, parent)
        self.setWindowTitle("Обучение")
        self.setFixedSize(660, 580)
        # Изменение применяем только при завершении обучения. В главном окне
        # callback дополнительно проверяет безопасное отключение записей.
        self._on_fin_instruments_changed = on_fin_instruments_changed
        self._on_recycle_bin_changed = on_recycle_bin_changed
        self._on_servers_changed = on_servers_changed
        self._initial_fin_instruments = config.get("show_fin_instruments", False)
        self._initial_recycle_bin = config.get("recycle_bin_enabled", False)
        self._initial_servers = config.get("show_servers", False)

        self._stack = QStackedWidget()
        for build in (self._slide_welcome, self._slide_tree, self._slide_card,
                      self._slide_fin_instruments, self._slide_servers,
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
            fin_selected = self.fin_instruments_check.isChecked()
            if fin_selected != self._initial_fin_instruments:
                if self._on_fin_instruments_changed is not None:
                    if not self._on_fin_instruments_changed(fin_selected):
                        self.fin_instruments_check.setChecked(
                            self._initial_fin_instruments)
                        return
                else:
                    self.config.set("show_fin_instruments", fin_selected)
                    self.config.save()
            recycle_selected = self.recycle_bin_check.isChecked()
            if recycle_selected != self._initial_recycle_bin:
                if self._on_recycle_bin_changed is not None:
                    if not self._on_recycle_bin_changed(recycle_selected):
                        self.recycle_bin_check.setChecked(
                            self._initial_recycle_bin)
                        return
                else:
                    self.config.set("recycle_bin_enabled", recycle_selected)
                    self.config.save()
            servers_selected = self.servers_check.isChecked()
            if servers_selected != self._initial_servers:
                if self._on_servers_changed is not None:
                    if not self._on_servers_changed(servers_selected):
                        self.servers_check.setChecked(self._initial_servers)
                        return
                else:
                    self.config.set("show_servers", servers_selected)
                    self.config.save()
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

    def _feature_row(self, caption: str, widget: QWidget):
        """Строка-функция: жирная подпись фиксированной ширины + живой виджет."""
        row = QHBoxLayout()
        label = QLabel(caption)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        label.setFixedWidth(140)
        row.addWidget(label)
        row.addWidget(widget, 1)
        return row

    @staticmethod
    def _mock_button(text: str) -> QPushButton:
        """Кнопка-муляж: выглядит как настоящая, действие — в настоящей карточке."""
        btn = QPushButton(text)
        btn.setEnabled(False)
        btn.setToolTip("Пример — работает в настоящей карточке")
        return btn

    def _chip(self, text: str) -> QLabel:
        """Плашка-чип: рамка outset, фон как у дерева."""
        _, _, text_color, tree_bg, _ = self._theme_colors()
        chip = QLabel(text)
        chip.setAlignment(Qt.AlignCenter)
        chip.setStyleSheet(
            f"QLabel {{ border: 1px outset #808080; background-color: {tree_bg}; "
            f"color: {text_color}; padding: 2px 6px; }}")
        return chip

    def _choice_panel(self, question: str, checkbox_text: str, checked: bool,
                      enabled_text: str, disabled_text: str):
        """Наглядный выбор: вопрос, активный чекбокс и явная расшифровка."""
        _, _, text_color, tree_bg, _ = self._theme_colors()
        panel = QFrame()
        panel.setStyleSheet(
            f"QFrame {{ border: 2px inset #808080; background-color: {tree_bg}; }}"
            f"QLabel {{ border: none; color: {text_color}; }}"
            f"QCheckBox {{ border: none; }}")
        panel_lay = QVBoxLayout(panel)
        panel_lay.setSpacing(8)

        prompt = QLabel(question)
        prompt_font = prompt.font()
        prompt_font.setBold(True)
        prompt.setFont(prompt_font)
        prompt.setWordWrap(True)
        panel_lay.addWidget(prompt)

        checkbox = QCheckBox(checkbox_text)
        checkbox.setChecked(checked)
        panel_lay.addWidget(checkbox)

        status = QLabel()
        status_font = status.font()
        status_font.setBold(True)
        status.setFont(status_font)
        status.setWordWrap(True)

        def sync_status(is_checked):
            choice = enabled_text if is_checked else disabled_text
            status.setText(f"ВАШ ВЫБОР: {choice}")

        checkbox.toggled.connect(sync_status)
        sync_status(checked)
        panel_lay.addWidget(status)
        return panel, checkbox, status

    # ----- Слайды -----

    def _slide_welcome(self):
        page, lay = self._slide(
            "Добро пожаловать в Хранилку",
            "Хранилка — оффлайн-хаб учётных записей. Все данные лежат в одном "
            "файле на вашем компьютере — никаких облаков и интернета.\n\n"
            "Это короткое обучение можно открыть в любой момент:"
            "Настройки → Поведение.",
        )
        lay.addStretch()
        title = QLabel("ХРАНИЛКА")
        title.setObjectName("appTitle")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        chips = QHBoxLayout()
        chips.addStretch()
        for text in ("БЕЗ ОБЛАКОВ", "ОДИН ФАЙЛ", "РЕТРО UI"):
            chips.addWidget(self._chip(text))
        chips.addStretch()
        lay.addLayout(chips)
        lay.addStretch()
        return page

    def _slide_tree(self):
        page, lay = self._slide(
            "Дерево: папки → сервисы → аккаунты",
            "Слева — дерево: папка группирует сервисы, сервис — аккаунты.",
        )
        font_name, font_size, text_color, tree_bg, main_bg = self._theme_colors()

        create_box = QWidget()
        create_lay = QHBoxLayout(create_box)
        create_lay.setContentsMargins(0, 0, 0, 0)
        for text in ("[+Папка]", "[+Сервис]", "[+Аккаунт]"):
            create_lay.addWidget(self._mock_button(text))
        create_lay.addStretch()
        lay.addLayout(self._feature_row("Создание", create_box))

        search_box = QWidget()
        search_lay = QHBoxLayout(search_box)
        search_lay.setContentsMargins(0, 0, 0, 0)
        search = QLineEdit("gmail")
        search.setReadOnly(True)
        search.setToolTip("Пример — поиск фильтрует настоящее дерево")
        search_lay.addWidget(search)
        lay.addLayout(self._feature_row("Поиск", search_box))

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
        tree.setFixedHeight(110)
        lay.addWidget(tree)
        hint = QLabel("* — избранное, отображается сверху при автоматических сортировках"
                      " · порядок меняется перетаскиванием в контексте одного родителя · ")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch()
        return page

    def _slide_card(self):
        page, lay = self._slide(
            "Карточка аккаунта",
            "Справа от дерева — карточка выбранного аккаунта: все данные "
            "в одном месте, каждое поле копируется кнопкой [КОП].",
        )
        login_box = QWidget()
        login_lay = QHBoxLayout(login_box)
        login_lay.setContentsMargins(0, 0, 0, 0)
        login = QLineEdit("личный@gmail.com")
        login.setReadOnly(True)
        login_lay.addWidget(login)
        login_lay.addWidget(self._mock_button("[КОП]"))
        lay.addLayout(self._feature_row("Логин", login_box))

        pass_box = QWidget()
        pass_lay = QHBoxLayout(pass_box)
        pass_lay.setContentsMargins(0, 0, 0, 0)
        field = QLineEdit("hunter2")
        field.setReadOnly(True)
        field.setEchoMode(QLineEdit.Password)
        pass_lay.addWidget(field)
        reveal = QPushButton("[*]")
        reveal.setFixedWidth(46)
        reveal.setToolTip("Показать/скрыть")
        reveal.clicked.connect(lambda: field.setEchoMode(
            QLineEdit.Normal if field.echoMode() == QLineEdit.Password
            else QLineEdit.Password))
        pass_lay.addWidget(reveal)
        pass_lay.addWidget(self._mock_button("[КОП]"))
        lay.addLayout(self._feature_row("Пароль", pass_box))

        tabs_box = QWidget()
        tabs_lay = QHBoxLayout(tabs_box)
        tabs_lay.setContentsMargins(0, 0, 0, 0)
        tabs_lay.setSpacing(4)
        for name in ("Связь аккаунтов", "Персона", "Коды и секреты", "Галерея"):
            tabs_lay.addWidget(self._chip(name))
        tabs_lay.addStretch()
        lay.addLayout(self._feature_row("Можно хранить", tabs_box))

        edits_box = QWidget()
        edits_lay = QHBoxLayout(edits_box)
        edits_lay.setContentsMargins(0, 0, 0, 0)
        marker = QLabel("● НЕ СОХРАНЕНО")
        marker_font = marker.font()
        marker_font.setBold(True)
        marker.setFont(marker_font)
        edits_lay.addWidget(marker)
        edits_lay.addStretch()
        edits_lay.addWidget(self._mock_button("Сохранить"))
        lay.addLayout(self._feature_row("Правки", edits_box))

        hint = QLabel("Несохранённые правки не теряются при переключении, "
                      "но помечаются в дереве до нажатия «Сохранить».")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch()
        return page

    def _slide_fin_instruments(self):
        page, lay = self._slide(
            "Нужны ли вам финансовые инструменты?",
            "Хранилка умеет хранить банковские карты и криптокошельки рядом "
            "с аккаунтами. Выберите, показывать ли эти возможности в интерфейсе.",
        )

        preview = QFrame()
        preview.setFrameShape(QFrame.StyledPanel)
        preview_lay = QVBoxLayout(preview)
        preview_lay.setSpacing(8)
        preview_lay.addWidget(QLabel("[КАРТА]  •••• 6588   |   [КОШЕЛЁК] 0x7A…91"))
        linked = QLabel("Свяжите инструмент с аккаунтом — переход работает в обе стороны.")
        linked.setWordWrap(True)
        preview_lay.addWidget(linked)
        lay.addWidget(preview)

        choice, self.fin_instruments_check, self._fin_choice_status = (
            self._choice_panel(
                "Использовать карты и криптокошельки?",
                "Да, использовать финансовые инструменты",
                self._initial_fin_instruments,
                "ИСПОЛЬЗОВАТЬ — разделы карт и кошельков будут доступны.",
                "НЕ ИСПОЛЬЗОВАТЬ — эти разделы будут скрыты."))
        self.fin_instruments_check.setToolTip(
            "После завершения обучения настройка сразу применится к интерфейсу.")
        lay.addWidget(choice)

        hint = QLabel(
            "Если позже передумаете: Настройки → Опции. Отключение только "
            "скрывает финансовые записи из интерфейса и не удаляет их из базы.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch()
        return page

    def _slide_servers(self):
        page, lay = self._slide(
            "Управляете VPS-серверами?",
            "Хранилка умеет хранить данные серверов рядом с аккаунтами: "
            "хост и SSH-порт, пользователей ОС, SSH-ключи, панели вроде "
            "3x-ui — и привязывать сервер к аккаунту провайдера (хостера).",
        )

        preview = QFrame()
        preview.setFrameShape(QFrame.StyledPanel)
        preview_lay = QVBoxLayout(preview)
        preview_lay.setSpacing(8)
        preview_lay.addWidget(QLabel(
            "[СЕРВЕР] 192.0.2.10:22   |   root, deploy   |   [3x-ui]"))
        linked = QLabel("Свяжите сервер с аккаунтом хостера — переход работает "
                        "в обе стороны, как у карт и кошельков.")
        linked.setWordWrap(True)
        preview_lay.addWidget(linked)
        lay.addWidget(preview)

        choice, self.servers_check, self._servers_choice_status = (
            self._choice_panel(
                "Показывать VPS-серверы?",
                "Да, использовать VPS-серверы",
                self._initial_servers,
                "ИСПОЛЬЗОВАТЬ — раздел серверов будет доступен.",
                "НЕ ИСПОЛЬЗОВАТЬ — этот раздел будет скрыт."))
        self.servers_check.setToolTip(
            "После завершения обучения настройка сразу применится к интерфейсу.")
        lay.addWidget(choice)

        hint = QLabel(
            "Если позже передумаете: Настройки → Опции. Отключение только "
            "скрывает серверы из интерфейса и не удаляет их из базы.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch()
        return page

    def _slide_security(self):
        page, lay = self._slide(
            "Защитите свои данные",
            "ВАЖНО: по умолчанию шифрование ВЫКЛЮЧЕНО — файл базы прочитает "
            "любой, кто до него доберётся.",
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

        for text, tip in (
            ("Мастер-пароль — шифрует файл базы",
             "Включается в Настройки → Безопасность"),
            ("Автоблокировка — при простое",
             "Программа сама запирается без вас"),
            ("Автоочистка буфера — скопированное не остаётся",
             "Пароль не висит в буфере обмена"),
            ("Защита от скриншотов",
             "Окно не попадает в снимки экрана"),
        ):
            box = QCheckBox(text)
            box.setChecked(True)
            box.setEnabled(False)
            box.setToolTip(tip)
            lay.addWidget(box)

        recovery = QLabel("[!] Код восстановления показывается ОДИН раз — "
                          "сохраните его.")
        recovery_font = recovery.font()
        recovery_font.setBold(True)
        recovery.setFont(recovery_font)
        recovery.setWordWrap(True)
        lay.addWidget(recovery)
        lay.addWidget(QLabel("Все переключатели: Настройки → Безопасность."))
        lay.addStretch()
        return page

    def _slide_tools(self):
        page, lay = self._slide(
            "Инструменты",
            "Генераторы, напоминания и экспорт — всё встроено.",
        )
        pass_box = QWidget()
        pass_lay = QHBoxLayout(pass_box)
        pass_lay.setContentsMargins(0, 0, 0, 0)
        sample = QLineEdit("Kq7#vR2$wLx9")
        sample.setReadOnly(True)
        pass_lay.addWidget(sample)
        pass_lay.addWidget(self._mock_button("Сгенерировать"))
        lay.addLayout(self._feature_row("Пароль", pass_box))

        pd_box = QWidget()
        pd_lay = QHBoxLayout(pd_box)
        pd_lay.setContentsMargins(0, 0, 0, 0)
        pd = QLineEdit("Менделеев Дмитрий, 27.01.1834, адрес! ")
        pd.setReadOnly(True)
        pd.setToolTip("Тестовые личные данные для регистраций")
        pd_lay.addWidget(pd)
        pd_lay.addWidget(self._chip("РУ"))
        pd_lay.addWidget(self._chip("США"))
        lay.addLayout(self._feature_row("Личные данные", pd_box))

        stale = QLabel("(i) личный@gmail.com  [!]  ← пора менять пароль")
        lay.addLayout(self._feature_row("Напоминание", stale))

        export_box = QWidget()
        export_lay = QHBoxLayout(export_box)
        export_lay.setContentsMargins(0, 0, 0, 0)
        for fmt in ("TXT", "CSV", "XLSX", "HTML"):
            export_lay.addWidget(self._mock_button(f"[{fmt}]"))
        export_lay.addStretch()
        lay.addLayout(self._feature_row("Экспорт", export_box))

        choice, self.recycle_bin_check, self._recycle_choice_status = (
            self._choice_panel(
                "Нужна ли вам корзина для удалённых записей?",
                "Да, использовать корзину",
                self._initial_recycle_bin,
                "ИСПОЛЬЗОВАТЬ — удалённые записи можно будет восстановить.",
                "НЕ ИСПОЛЬЗОВАТЬ — записи будут удаляться сразу и безвозвратно."))
        self.recycle_bin_check.setToolTip(
            "После завершения обучения настройка сразу применится к интерфейсу.")
        lay.addWidget(choice)
        location = QLabel("Изменить позже: Настройки → Опции.")
        location.setWordWrap(True)
        lay.addWidget(location)
        lay.addStretch()
        return page

    def _slide_final(self):
        page, lay = self._slide(
            "Настройте под себя",
            "Темы, свой шрифт и цвета — Настройки → Внешний вид.",
        )
        _, _, text_color, tree_bg, _ = self._theme_colors()

        swatch_box = QWidget()
        swatches = QHBoxLayout(swatch_box)
        swatches.setContentsMargins(0, 0, 0, 0)
        for theme_name, colors in config_mod.RETRO_THEMES.items():
            square = QFrame()
            square.setFixedSize(18, 18)
            square.setToolTip(theme_name)
            square.setStyleSheet(f"background-color: {colors['main_bg']}; "
                                 f"border: 1px solid {text_color};")
            swatches.addWidget(square)
        current = self.config.get("selected_theme", "")
        swatches.addWidget(QLabel(f"← текущая: {current}"))
        swatches.addStretch()
        lay.addLayout(self._feature_row("Темы", swatch_box))

        keys = QFrame()
        keys.setStyleSheet(
            f"QFrame {{ border: 2px inset #808080; background-color: {tree_bg}; }}"
            f"QLabel {{ border: none; color: {text_color}; }}"
        )
        keys_lay = QVBoxLayout(keys)
        for line in ("Ctrl+F — перейти к поиску", "Ctrl+S — сохранить аккаунт",
                     "Ctrl+N — создать аккаунт"):
            keys_lay.addWidget(QLabel(line))
        lay.addLayout(self._feature_row("Шорткаты", keys))
        lay.addWidget(QLabel("Полный список и переназначение: "
                             "Настройки → Шорткаты."))

        ready = QLabel("Готово: создайте первую папку — и вперёд.")
        ready_font = ready.font()
        ready_font.setBold(True)
        ready.setFont(ready_font)
        lay.addWidget(ready)
        lay.addStretch()
        return page
