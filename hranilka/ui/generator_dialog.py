"""Диалог настроек генерации пароля (кнопка «ПАРАМЕТРЫ ГЕНЕРАЦИИ» на вкладке
«Логин и пароль»).

Две вкладки — «Пароль» и «Парольная фраза» — с опциями из password_gen и живым
предпросмотром. Кнопка «СГЕНЕРИРОВАТЬ ПАРОЛЬ» главного окна использует
настройки активной вкладки (режим запоминается в config под
password_gen.CONFIG_KEY). Диалог только пишет config — сохраняет вызывающий;
пароль из предпросмотра доступен через preview_text() (при «Сохранить»
подставляется в поле пароля аккаунта)."""
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

import password_gen
from flowlayout import WrappingTabWidget
from theme import ThemedDialog


class GeneratorSettingsDialog(ThemedDialog):
    """Настройки генерации: собирает GeneratorSettings из виджетов, при OK
    пишет их в config (без save — это делает вызывающий)."""

    def __init__(self, config, parent=None):
        super().__init__(config, parent)
        self.setWindowTitle("Генерация пароля")
        self.setModal(True)
        self.setMinimumWidth(480)
        s = password_gen.settings_from_config(
            config.get(password_gen.CONFIG_KEY, {}) or {})
        self._build_ui(s)
        self._refresh_preview()

    # ─── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self, s):
        lay = self.body

        preview_row = QHBoxLayout()
        preview_row.setSpacing(5)
        self._preview = QLineEdit()
        self._preview.setReadOnly(True)
        preview_row.addWidget(self._preview)
        refresh_btn = QPushButton("[ОБН]")
        refresh_btn.setFixedWidth(60)
        refresh_btn.setToolTip("Сгенерировать новый пример")
        refresh_btn.clicked.connect(self._refresh_preview)
        preview_row.addWidget(refresh_btn)
        lay.addLayout(preview_row)

        self._tabs = WrappingTabWidget(self)
        self._tabs.addTab(self._page_password(s.password), "Пароль")
        self._tabs.addTab(self._page_phrase(s.phrase), "Парольная фраза")
        self._tabs.setCurrentIndex(
            1 if s.mode == password_gen.MODE_PHRASE else 0)
        self._tabs.currentChanged.connect(self._refresh_preview)
        lay.addWidget(self._tabs)

        hint = QLabel("Кнопка «СГЕНЕРИРОВАТЬ ПАРОЛЬ» использует настройки "
                      "активной вкладки.")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Сохранить")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    def _spin(self, lo, hi, value):
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(value)
        spin.valueChanged.connect(self._refresh_preview)
        return spin

    def _check(self, text, checked):
        box = QCheckBox(text)
        box.setChecked(checked)
        box.toggled.connect(self._refresh_preview)
        return box

    def _page_password(self, pw):
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(8)

        len_row = QHBoxLayout()
        len_row.addWidget(QLabel("Длина:"))
        self._pw_length = self._spin(
            password_gen.LENGTH_MIN, password_gen.LENGTH_MAX, pw.length)
        len_row.addWidget(self._pw_length)
        len_row.addStretch()
        lay.addLayout(len_row)

        lay.addWidget(QLabel("Включить:"))
        charset_row = QHBoxLayout()
        self._pw_upper = self._check("A-Z", pw.use_upper)
        self._pw_lower = self._check("a-z", pw.use_lower)
        self._pw_digits = self._check("0-9", pw.use_digits)
        self._pw_symbols = self._check("!@#$%^&*", pw.use_symbols)
        self._charset_boxes = (self._pw_upper, self._pw_lower,
                               self._pw_digits, self._pw_symbols)
        for box in self._charset_boxes:
            box.toggled.connect(self._guard_charsets)
            charset_row.addWidget(box)
        charset_row.addStretch()
        lay.addLayout(charset_row)

        min_row = QHBoxLayout()
        min_digits_lbl = QLabel("Минимум цифр:")
        min_row.addWidget(min_digits_lbl)
        self._pw_min_digits = self._spin(0, password_gen.MIN_COUNT_MAX,
                                         pw.min_digits)
        min_row.addWidget(self._pw_min_digits)
        min_row.addSpacing(16)
        min_symbols_lbl = QLabel("Минимум спецсимволов:")
        min_row.addWidget(min_symbols_lbl)
        self._pw_min_symbols = self._spin(0, password_gen.MIN_COUNT_MAX,
                                          pw.min_symbols)
        min_row.addWidget(self._pw_min_symbols)
        min_row.addStretch()
        lay.addLayout(min_row)
        # Минимум имеет смысл только при включённом наборе: поле и подпись
        # блокируются и визуально приглушаются (стили :disabled в theme.py).
        for box, widgets in ((self._pw_digits, (self._pw_min_digits,
                                                min_digits_lbl)),
                             (self._pw_symbols, (self._pw_min_symbols,
                                                 min_symbols_lbl))):
            for w in widgets:
                box.toggled.connect(w.setEnabled)
                w.setEnabled(box.isChecked())

        self._pw_similar = self._check(
            "Исключать похожие символы (0, O, 1, l, I)", pw.exclude_similar)
        lay.addWidget(self._pw_similar)
        lay.addStretch()
        return page

    def _page_phrase(self, ph):
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(8)

        words_row = QHBoxLayout()
        words_row.addWidget(QLabel("Количество слов:"))
        self._ph_words = self._spin(
            password_gen.WORDS_MIN, password_gen.WORDS_MAX, ph.words)
        words_row.addWidget(self._ph_words)
        words_row.addSpacing(16)
        words_row.addWidget(QLabel("Разделитель:"))
        self._ph_separator = QLineEdit(ph.separator)
        self._ph_separator.setMaxLength(password_gen.SEPARATOR_MAX_LEN)
        self._ph_separator.setFixedWidth(60)
        self._ph_separator.textChanged.connect(self._refresh_preview)
        words_row.addWidget(self._ph_separator)
        words_row.addStretch()
        lay.addLayout(words_row)

        self._ph_capitalize = self._check("Слова с заглавной буквы",
                                          ph.capitalize)
        lay.addWidget(self._ph_capitalize)
        self._ph_digit = self._check("Добавить цифру", ph.add_digit)
        lay.addWidget(self._ph_digit)
        lay.addStretch()
        return page

    # ─── Логика ──────────────────────────────────────────────────────────────

    def _guard_charsets(self, checked):
        """Не даём снять последний набор символов — пароль без алфавита
        невозможен (generate_password кинул бы ValueError)."""
        if not checked and not any(b.isChecked() for b in self._charset_boxes):
            self.sender().setChecked(True)

    def collect_settings(self):
        """GeneratorSettings из текущего состояния виджетов."""
        return password_gen.GeneratorSettings(
            mode=(password_gen.MODE_PHRASE if self._tabs.currentIndex() == 1
                  else password_gen.MODE_PASSWORD),
            password=password_gen.PasswordOptions(
                length=self._pw_length.value(),
                use_upper=self._pw_upper.isChecked(),
                use_lower=self._pw_lower.isChecked(),
                use_digits=self._pw_digits.isChecked(),
                use_symbols=self._pw_symbols.isChecked(),
                min_digits=self._pw_min_digits.value(),
                min_symbols=self._pw_min_symbols.value(),
                exclude_similar=self._pw_similar.isChecked(),
            ),
            phrase=password_gen.PassphraseOptions(
                words=self._ph_words.value(),
                separator=self._ph_separator.text(),
                capitalize=self._ph_capitalize.isChecked(),
                add_digit=self._ph_digit.isChecked(),
            ),
        )

    def _refresh_preview(self, *_args):
        self._preview.setText(password_gen.generate(self.collect_settings()))

    def preview_text(self):
        """Пароль из поля предпросмотра (для подстановки в карточку)."""
        return self._preview.text()

    def accept(self):
        self.config.set(password_gen.CONFIG_KEY,
                        password_gen.settings_to_config(self.collect_settings()))
        super().accept()
