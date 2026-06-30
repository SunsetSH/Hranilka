from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout,
    QComboBox, QPushButton, QLabel,
    QCheckBox, QSpinBox, QLineEdit, QListWidget, QListWidgetItem,
    QFileDialog, QWidget, QFrame, QDialog, QApplication,
    QRadioButton, QButtonGroup, QScrollArea,
)
from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtGui import QFont, QIntValidator, QFontDatabase, QKeySequence

# Курируемый набор «программистских» моноширинных шрифтов в духе 2000-х.
# Cascadia Code — по умолчанию, удалять нельзя. Показываются только те, что
# реально установлены в системе (плюс текущий выбранный — на всякий случай).
CODING_FONTS = [
    "Cascadia Code", "Consolas", "Courier New",
    "Lucida Console", "Lucida Sans Typewriter", "Fixedsys",
    "Source Code Pro", "JetBrains Mono", "Fira Code", "DejaVu Sans Mono",
    "Liberation Mono",
]

from config import RETRO_THEMES
from PySide6.QtWidgets import QColorDialog
from theme import ThemedDialog, themed_confirm, themed_info, dialog_stylesheet
from flowlayout import WrappingTabWidget
import backup as bk
import crypto_store as cs
import util
import export
import shortcuts


class SettingsDialog(ThemedDialog):
    settings_applied = Signal()      # финальное «Применить» (полная переинициализация)
    appearance_changed = Signal()    # живой предпросмотр шрифта/темы/цвета (только стили)

    def __init__(self, config, parent=None):
        super().__init__(config, parent)
        self.setWindowTitle("Настройки")
        self.setModal(True)
        # Ширину держим, чтобы строки шорткатов помещались; высоту не форсируем —
        # реальный минимум задаёт самая высокая вкладка (а её мы ужали).
        self.setMinimumSize(720, 400)
        self._db_path = "hranilka.db"
        self._db = None
        self._run_vault_op = self._default_vault_op
        self._restore_handler = self._default_restore_handler
        self._delete_all_confirmed = False
        self._restore_done = False
        # Снимок цветов/шрифта для восстановления при отмене / откате
        # живого предпросмотра.
        self._orig_colors = {
            "text_color":    config.get("text_color",    "#000000"),
            "tree_bg_color": config.get("tree_bg_color", "#FFFFFF"),
            "main_bg_color": config.get("main_bg_color", "#F0F0F0"),
        }
        self._orig_font = config.get("font", "Cascadia Code")
        self._orig_font_size = config.get("font_size", 14)
        self._setup_ui()

    def set_db_path(self, path: str):
        self._db_path = path

    def set_db(self, db):
        """Ссылка на активную БД — нужна вкладке «Шифрование»."""
        self._db = db
        if hasattr(self, "_refresh_encryption_page"):
            self._refresh_encryption_page()

    def set_vault_runner(self, runner):
        """Внедрить гейт монопольного доступа к файлу-БД из главного окна.

        Привилегированные операции (вкл/выкл шифрования, смена пароля,
        бэкап/восстановление) пишут файл синхронно, пока крутится event-loop
        модального диалога. Через гейт они выполняются, когда фоновый воркер
        заведомо не пишет (H3-01). runner(fn) -> (ok, result_or_error)."""
        self._run_vault_op = runner

    @staticmethod
    def _default_vault_op(fn):
        """Запасной runner, если гейт не внедрён (диалог вне главного окна):
        просто выполняет операцию, приводя сигнатуру к (ok, result_or_error)."""
        try:
            return True, fn()
        except Exception as e:                       # noqa: BLE001
            return False, str(e)

    def set_restore_handler(self, handler):
        """Внедрить обработчик восстановления из бэкапа (из главного окна).

        Восстановление должно закрыть SQLite-соединение ДО замены файла (иначе
        на Windows os.replace падает с WinError 5 в plaintext-режиме), поэтому
        всю последовательность ведёт MainWindow. handler(path) -> (ok, err)."""
        self._restore_handler = handler

    @staticmethod
    def _default_restore_handler(path):
        return False, "Восстановление недоступно вне главного окна."

    def _setup_ui(self):
        self._tabs = WrappingTabWidget(self)
        self._tabs.addTab(self._page_appearance(), "Внешний вид")
        self._tabs.addTab(self._page_security(),   "Безопасность")
        self._tabs.addTab(self._page_backup(),     "Бэкапы")
        self._tabs.addTab(self._page_data(),       "Данные")
        self._tabs.addTab(self._page_behavior(),   "Поведение")
        self.body.addWidget(self._tabs, 1)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        self.body.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        apply_btn = QPushButton("Применить и закрыть")
        apply_btn.clicked.connect(self._apply_settings)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(cancel_btn)
        self.body.addLayout(btn_row)

        self._apply_group_fonts()
        # Снимок значений всех настроек для определения несохранённых изменений
        # при закрытии (блок шифрования сюда не входит — он применяется сразу).
        self._initial_settings = self._collect_settings()

    def _apply_group_fonts(self):
        """Шрифт заголовков групп через стили Qt применяет ненадёжно, поэтому
        задаём его виджетам QGroupBox напрямую: выбранный шрифт, кегль −2."""
        font_name = self.config.get("font", "Cascadia Code")
        font_size = self.config.get("font_size", 14)
        gf = QFont(font_name, max(1, font_size - 2))
        for gb in self.findChildren(QGroupBox):
            gb.setFont(gf)

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

    # ─── Вкладка: Безопасность ───────────────────────────────────────────────

    def _page_security(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.setContentsMargins(0, 8, 0, 0)

        ss_group = QGroupBox("Защита экрана")
        sl = QVBoxLayout(ss_group)
        self.ss_protect_check = QCheckBox("Скрыть окно из скриншотов и записей экрана")
        self.ss_protect_check.setChecked(self.config.get("screenshot_protect", False))
        sl.addWidget(self.ss_protect_check)
        note = QLabel("Использует Windows API (SetWindowDisplayAffinity).\n"
                       "Вступает в силу после применения настроек.")
        note.setWordWrap(True)
        sl.addWidget(note)
        lay.addWidget(ss_group)

        idle_group = QGroupBox("Автоблокировка")
        ilf = QFormLayout(idle_group)
        self.idle_mins = QLineEdit(str(self.config.get("idle_lock_mins", 0)))
        self.idle_mins.setValidator(QIntValidator(0, 120, self))
        ilf.addRow("Скрыть данные при простое:", self.idle_mins)
        ilf.addRow("", QLabel("в минутах, 0 — не скрывать"))
        lay.addWidget(idle_group)

        self._build_encryption_groups(lay)

        lay.addStretch()
        return w

    # ─── Вкладка: Шифрование ─────────────────────────────────────────────────

    _PRESET_LABELS = [
        ("fast",     "Быстро (~32 МБ ОЗУ)"),
        ("balanced", "Сбалансировано (~64 МБ ОЗУ)"),
        ("paranoid", "Параноик (~256 МБ ОЗУ)"),
    ]

    def _build_encryption_groups(self, lay):
        """Встраивает блок настроек шифрования в переданный layout
        (вкладка «Безопасность»)."""
        self._enc_status = QLabel("")
        self._enc_status.setWordWrap(True)

        # --- Группа «выключено»: включение шифрования ---
        self._enc_off_group = QGroupBox("Шифрование базы")
        offl = QVBoxLayout(self._enc_off_group)
        info = QLabel(
            "Шифрует весь файл базы (AES-256-GCM, ключ из мастер-пароля "
            "по Argon2id). Без пароля или recovery-кода доступ к данным "
            "невозможен."
        )
        info.setWordWrap(True)
        offl.addWidget(info)
        offl.addWidget(self._enc_status)
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Стойкость ключа:"))
        self.enc_preset_combo = QComboBox()
        for _, label in self._PRESET_LABELS:
            self.enc_preset_combo.addItem(label)
        self.enc_preset_combo.setCurrentIndex(self._preset_index(
            self.config.get("argon2_preset", "balanced")))
        preset_row.addWidget(self.enc_preset_combo, 1)
        offl.addLayout(preset_row)
        enable_btn = QPushButton("Включить шифрование…")
        enable_btn.clicked.connect(self._enc_enable)
        offl.addWidget(enable_btn)
        lay.addWidget(self._enc_off_group)

        # --- Группа «включено»: управление ---
        self._enc_on_group = QGroupBox("Управление шифрованием")
        onl = QVBoxLayout(self._enc_on_group)
        onl.addWidget(self._enc_status_on())
        chpw_btn = QPushButton("Сменить мастер-пароль…")
        chpw_btn.clicked.connect(self._enc_change_password)
        onl.addWidget(chpw_btn)
        rec_btn = QPushButton("Обновить recovery-код…")
        rec_btn.clicked.connect(self._enc_regen_recovery)
        onl.addWidget(rec_btn)
        disable_btn = QPushButton("Отключить шифрование…")
        disable_btn.clicked.connect(self._enc_disable)
        onl.addWidget(disable_btn)
        lay.addWidget(self._enc_on_group)

        self._refresh_encryption_page()

    def _enc_status_on(self):
        """Вторая метка статуса для группы «включено» (для наглядности)."""
        self._enc_status2 = QLabel("")
        self._enc_status2.setWordWrap(True)
        return self._enc_status2

    def _preset_index(self, preset: str) -> int:
        for i, (key, _) in enumerate(self._PRESET_LABELS):
            if key == preset:
                return i
        return 1

    def _current_preset(self) -> str:
        return self._PRESET_LABELS[self.enc_preset_combo.currentIndex()][0]

    def _refresh_encryption_page(self):
        if not hasattr(self, "_enc_status"):
            return
        enabled = bool(self._db and getattr(self._db, "encrypted", False))
        self._enc_status.setText(
            "Состояние: шифрование выключено." if not enabled else "")
        if hasattr(self, "_enc_status2"):
            self._enc_status2.setText(
                "Состояние: шифрование ВКЛЮЧЕНО." if enabled else "")
        self._enc_off_group.setVisible(not enabled)
        self._enc_on_group.setVisible(enabled)

    def _enc_enable(self):
        if not self._db:
            return
        pw = self._ask_new_password("Установка мастер-пароля")
        if not pw:
            return
        preset = self._current_preset()
        ok, res = self._run_vault_op(lambda: self._db.enable_encryption(pw, preset))
        if not ok:
            themed_info(self.config, self, "Ошибка", f"Не удалось включить шифрование:\n{res}")
            return
        recovery = res
        self.config.set("encryption_enabled", True)
        self.config.set("argon2_preset", preset)
        self.config.save()
        self._show_recovery(recovery)
        self._refresh_encryption_page()
        themed_info(self.config, self, "Готово", "Шифрование включено.")

    def _enc_disable(self):
        if not self._db:
            return
        auth = self._ask_secret("Отключение шифрования",
                                "Подтвердите мастер-паролем или recovery-кодом:")
        if auth is None:
            return
        secret, is_rec = auth
        if not self._db.verify_secret(secret, is_rec):
            themed_info(self.config, self, "Ошибка", "Неверный пароль или recovery-код.")
            return
        if not themed_confirm(self.config, self, "Отключение шифрования",
                              "База будет сохранена в открытом (незашифрованном) виде. Продолжить?"):
            return
        ok, res = self._run_vault_op(self._db.disable_encryption)
        if not ok:
            themed_info(self.config, self, "Ошибка", f"Не удалось отключить шифрование:\n{res}")
            return
        self.config.set("encryption_enabled", False)
        self.config.save()
        self._refresh_encryption_page()
        themed_info(self.config, self, "Готово", "Шифрование отключено.")

    def _enc_change_password(self):
        if not self._db:
            return
        auth = self._ask_secret("Смена мастер-пароля",
                                "Подтвердите текущим паролем или recovery-кодом:")
        if auth is None:
            return
        secret, is_rec = auth
        if not self._db.verify_secret(secret, is_rec):
            themed_info(self.config, self, "Ошибка", "Неверный пароль или recovery-код.")
            return
        new = self._ask_new_password("Новый мастер-пароль")
        if not new:
            return
        ok, res = self._run_vault_op(lambda: self._db.change_master_password(new))
        if not ok:
            themed_info(self.config, self, "Ошибка", f"Не удалось сменить пароль:\n{res}")
            return
        themed_info(self.config, self, "Готово", "Мастер-пароль изменён.")

    def _enc_regen_recovery(self):
        if not self._db:
            return
        auth = self._ask_secret("Обновление recovery-кода",
                                "Подтвердите мастер-паролем или текущим recovery-кодом:")
        if auth is None:
            return
        secret, is_rec = auth
        if not self._db.verify_secret(secret, is_rec):
            themed_info(self.config, self, "Ошибка", "Неверный пароль или recovery-код.")
            return
        if not themed_confirm(self.config, self, "Обновление recovery-кода",
                              "Старый recovery-код перестанет действовать. Продолжить?"):
            return
        ok, res = self._run_vault_op(self._db.regenerate_recovery_code)
        if not ok:
            themed_info(self.config, self, "Ошибка", f"Не удалось обновить код:\n{res}")
            return
        self._show_recovery(res)

    def _ask_secret(self, title: str, prompt: str):
        """Ввод мастер-пароля ИЛИ recovery-кода. Возвращает (secret, is_recovery)
        или None (отмена). Есть переключатель режима и вставка из буфера."""
        d = ThemedDialog(self.config, self)
        d.setWindowTitle(title)
        d.setMinimumWidth(400)
        lay = d.body
        lay.addWidget(QLabel(prompt))
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.Password)
        lay.addWidget(edit)

        rec_check = QCheckBox("Использовать recovery-код")

        def on_toggle(use_rec):
            edit.setEchoMode(QLineEdit.Normal if use_rec else QLineEdit.Password)
            edit.setPlaceholderText("XXXX-XXXX-…" if use_rec else "")
            edit.clear()
        rec_check.toggled.connect(on_toggle)
        lay.addWidget(rec_check)

        paste_row = QHBoxLayout()
        paste_btn = QPushButton("Вставить из буфера")
        paste_btn.clicked.connect(lambda: edit.setText(QApplication.clipboard().text()))
        paste_row.addWidget(paste_btn)
        paste_row.addStretch()
        lay.addLayout(paste_row)

        row = QHBoxLayout()
        row.addStretch()
        ok = QPushButton("OK"); ok.setDefault(True); ok.clicked.connect(d.accept)
        cancel = QPushButton("Отмена"); cancel.clicked.connect(d.reject)
        row.addWidget(ok); row.addWidget(cancel)
        lay.addLayout(row)
        edit.returnPressed.connect(d.accept)
        QTimer.singleShot(0, edit.setFocus)
        if d.exec() != QDialog.Accepted:
            return None
        return edit.text().strip(), rec_check.isChecked()

    def _ask_password(self, title: str, prompt: str):
        """Однопольный ввод пароля. Возвращает строку или None (отмена)."""
        d = ThemedDialog(self.config, self)
        d.setWindowTitle(title)
        d.setMinimumWidth(380)
        lay = d.body
        lay.addWidget(QLabel(prompt))
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.Password)
        lay.addWidget(edit)
        row = QHBoxLayout()
        row.addStretch()
        ok = QPushButton("OK"); ok.setDefault(True); ok.clicked.connect(d.accept)
        cancel = QPushButton("Отмена"); cancel.clicked.connect(d.reject)
        row.addWidget(ok); row.addWidget(cancel)
        lay.addLayout(row)
        edit.returnPressed.connect(d.accept)
        QTimer.singleShot(0, edit.setFocus)
        if d.exec() != QDialog.Accepted:
            return None
        return edit.text()

    @staticmethod
    def _generate_password(length: int = 16) -> str:
        """Случайный пароль (как для аккаунтов: буквы, цифры, спецсимволы)."""
        return util.generate_password(length)

    def _ask_new_password(self, title: str):
        """Ввод нового пароля с подтверждением. Возвращает строку или None."""
        d = ThemedDialog(self.config, self)
        d.setWindowTitle(title)
        d.setMinimumWidth(400)
        lay = d.body
        lay.addWidget(QLabel("Новый мастер-пароль:"))
        e1 = QLineEdit(); e1.setEchoMode(QLineEdit.Password)
        lay.addWidget(e1)
        lay.addWidget(QLabel("Повторите пароль:"))
        e2 = QLineEdit(); e2.setEchoMode(QLineEdit.Password)
        lay.addWidget(e2)

        tools = QHBoxLayout()
        gen_btn = QPushButton("Сгенерировать пароль")
        copy_btn = QPushButton("Копировать")

        def do_generate():
            pw = self._generate_password()
            e1.setText(pw)
            e2.setText(pw)
            # Показать сгенерированный пароль, чтобы пользователь мог его увидеть.
            e1.setEchoMode(QLineEdit.Normal)
            e2.setEchoMode(QLineEdit.Normal)
            err.setText("")

        def do_copy():
            if e1.text():
                QApplication.clipboard().setText(e1.text())
                err.setText("Пароль скопирован в буфер обмена.")
        gen_btn.clicked.connect(do_generate)
        copy_btn.clicked.connect(do_copy)
        tools.addWidget(gen_btn)
        tools.addWidget(copy_btn)
        tools.addStretch()
        lay.addLayout(tools)

        err = QLabel(""); err.setWordWrap(True)
        lay.addWidget(err)
        row = QHBoxLayout()
        row.addStretch()
        ok = QPushButton("OK"); ok.setDefault(True)
        cancel = QPushButton("Отмена"); cancel.clicked.connect(d.reject)
        row.addWidget(ok); row.addWidget(cancel)
        lay.addLayout(row)

        def try_ok():
            if len(e1.text()) < 12:
                err.setText("Пароль слишком короткий (минимум 12 символов).")
                return
            if e1.text() != e2.text():
                err.setText("Пароли не совпадают.")
                return
            d.accept()
        ok.clicked.connect(try_ok)
        e2.returnPressed.connect(try_ok)
        QTimer.singleShot(0, e1.setFocus)
        if d.exec() != QDialog.Accepted:
            return None
        return e1.text()

    def _show_recovery(self, code: str):
        """Показать recovery-код один раз: копировать / сохранить в файл."""
        from PySide6.QtWidgets import QApplication
        d = ThemedDialog(self.config, self)
        d.setWindowTitle("Recovery-код")
        d.setMinimumWidth(440)
        lay = d.body
        lay.addWidget(QLabel(
            "Сохраните этот код в надёжном месте. Он даёт доступ к данным,\n"
            "если вы забудете мастер-пароль. Код показывается ОДИН РАЗ."))
        code_edit = QLineEdit(code)
        code_edit.setReadOnly(True)
        code_edit.setCursorPosition(0)
        lay.addWidget(code_edit)
        row = QHBoxLayout()
        copy_btn = QPushButton("Копировать")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(code))
        save_btn = QPushButton("Сохранить в файл…")

        def save_to_file():
            path, _ = QFileDialog.getSaveFileName(
                d, "Сохранить recovery-код", "hranilka-recovery.txt",
                "Текстовые файлы (*.txt)")
            if path:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("Хранилка — recovery-код\n" + code + "\n")
                except OSError as e:
                    themed_info(self.config, d, "Ошибка", f"Не удалось сохранить:\n{e}")
        save_btn.clicked.connect(save_to_file)
        row.addWidget(copy_btn); row.addWidget(save_btn)
        row.addStretch()
        ok = QPushButton("Я сохранил код"); ok.setDefault(True); ok.clicked.connect(d.accept)
        row.addWidget(ok)
        lay.addLayout(row)
        d.exec()

    # ─── Вкладка: Бэкапы ────────────────────────────────────────────────────

    def _page_backup(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.setContentsMargins(0, 8, 0, 0)

        path_group = QGroupBox("Папка для бэкапов")
        pl = QHBoxLayout(path_group)
        self.backup_folder_edit = QLineEdit(self.config.get("backup_folder", ""))
        self.backup_folder_edit.setPlaceholderText("Путь к папке бэкапов...")
        self.backup_folder_edit.textChanged.connect(self._refresh_backup_list)
        browse_btn = QPushButton("Обзор...")
        browse_btn.clicked.connect(self._browse_backup_folder)
        pl.addWidget(self.backup_folder_edit, 1)
        pl.addWidget(browse_btn)
        lay.addWidget(path_group)

        opt_group = QGroupBox("Параметры")
        of = QFormLayout(opt_group)
        self.backup_auto_check = QCheckBox("Авто-бэкап при закрытии (если были изменения)")
        self.backup_auto_check.setChecked(self.config.get("backup_auto_on_close", False))
        self.backup_keep_spin = QSpinBox()
        self.backup_keep_spin.setRange(1, 50)
        self.backup_keep_spin.setValue(self.config.get("backup_keep_count", 5))
        of.addRow(self.backup_auto_check)
        of.addRow("Хранить последних:", self.backup_keep_spin)
        lay.addWidget(opt_group)

        act_group = QGroupBox("Действия")
        al = QVBoxLayout(act_group)
        make_btn = QPushButton("Создать бэкап сейчас")
        make_btn.clicked.connect(self._do_backup)
        al.addWidget(make_btn)

        al.addWidget(QLabel("Существующие бэкапы:"))
        self.backup_list = QListWidget()
        self.backup_list.setFixedHeight(110)
        al.addWidget(self.backup_list)
        self._refresh_backup_list()

        restore_btn = QPushButton("Восстановить выбранный бэкап")
        restore_btn.clicked.connect(self._do_restore)
        al.addWidget(restore_btn)
        lay.addWidget(act_group)

        lay.addStretch()
        return w

    def _do_export_all(self):
        if not self._db:
            return
        tree = self._db.export_subtree()
        dlg = ExportDialog(self.config, tree, "Вся база", self)
        dlg.exec()

    # ─── Вкладка: Данные ────────────────────────────────────────────────────

    def _page_data(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.setContentsMargins(0, 8, 0, 0)

        danger_group = QGroupBox("Удаление данных")
        dl = QVBoxLayout(danger_group)
        warn = QLabel(
            "ВНИМАНИЕ: удаление необратимо.\n\n"
            "На SSD-накопителях физическое уничтожение данных не гарантируется\n"
            "из-за особенностей работы контроллера NAND и механизма TRIM.\n\n"
            "Все аккаунты, папки, сервисы, вложения и бэкапы будут стёрты."
        )
        warn.setWordWrap(True)
        dl.addWidget(warn)

        del_btn = QPushButton("УДАЛИТЬ ВСЕ ДАННЫЕ")
        del_btn.setStyleSheet(
            "QPushButton { background-color: #8B0000; color: #FFFFFF; "
            "font-weight: bold; border: 2px outset #FF0000; }"
            "QPushButton:hover { background-color: #CC0000; border: 2px inset #FF0000; }"
        )
        del_btn.clicked.connect(self._delete_all)
        dl.addWidget(del_btn)
        lay.addWidget(danger_group)

        bin_group = QGroupBox("Корзина")
        bl = QVBoxLayout(bin_group)
        self.recycle_bin_check = QCheckBox("Удалять в корзину")
        self.recycle_bin_check.setChecked(self.config.get("recycle_bin_enabled", False))
        bl.addWidget(self.recycle_bin_check)
        bin_note = QLabel(
            "Если включено, удалённые аккаунты перемещаются в корзину, откуда их можно восстановить. Кнопка корзины появляется рядом с «Настройки».\n"
            "Если выключено, аккаунты удаляются сразу и безвозвратно."
        )
        bin_note.setWordWrap(True)
        bl.addWidget(bin_note)
        lay.addWidget(bin_group)

        exp_group = QGroupBox("Экспорт")
        el = QVBoxLayout(exp_group)
        el.addWidget(QLabel(
            "Выгрузка всей базы в читаемый формат (TXT/CSV/XLSX/HTML).\n"
            "Экспорт отдельной папки/сервиса/аккаунта — через ПКМ в дереве."))
        export_btn = QPushButton("Экспортировать всё…")
        export_btn.clicked.connect(self._do_export_all)
        el.addWidget(export_btn)
        lay.addWidget(exp_group)

        maint_group = QGroupBox("Обслуживание")
        ml = QVBoxLayout(maint_group)
        ml.addWidget(QLabel(
            "После удаления картинок/аккаунтов файл базы сам не уменьшается —\n"
            "освободившееся место остаётся внутри для повторного использования.\n"
            "«Сжать базу» физически уменьшает файл (VACUUM)."))
        vacuum_btn = QPushButton("Сжать базу (VACUUM)")
        vacuum_btn.clicked.connect(self._do_vacuum)
        ml.addWidget(vacuum_btn)
        lay.addWidget(maint_group)

        lay.addStretch()
        return w

    def _do_vacuum(self):
        if self._db is None:
            return
        # Монопольно через гейт: VACUUM блокирует БД и не должен конкурировать с
        # фоновой записью; в шифр. режиме после сжатия перезапишется контейнер.
        ok, err = self._run_vault_op(self._db.vacuum)
        if not ok:
            themed_info(self.config, self, "Ошибка", f"Не удалось сжать базу:\n{err}")
            return
        themed_info(self.config, self, "Готово",
                    "База сжата: свободные страницы освобождены, файл уменьшен.")

    # ─── Вкладка: Поведение ──────────────────────────────────────────────────

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
        self.clip_clear_secs = QLineEdit(str(self.config.get("clipboard_clear_secs", 0)))
        self.clip_clear_secs.setValidator(QIntValidator(0, 3600, self))
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

        # Шорткаты — отдельной секцией внизу вкладки «Поведение»
        lay.addWidget(self._build_shortcuts_group(), 1)
        return w

    # ─── Секция: Шорткаты (внутри вкладки «Поведение») ───────────────────────

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
        # Компактная высота, чтобы вкладка «Поведение» (а значит и всё окно
        # настроек) не вырастала — список листается внутри прокрутки. На
        # растянутом окне блок занимает доступное место по высоте.
        scroll.setMinimumHeight(72)
        scroll.setMaximumHeight(200)

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

    # ─── Вспомогательные ────────────────────────────────────────────────────

    def _refresh_color_btns(self):
        for btn, key, label in (
            (self.text_color_btn, "text_color",    "Текст"),
            (self.tree_bg_btn,    "tree_bg_color", "Фон дерева"),
            (self.main_bg_btn,    "main_bg_color", "Основной фон"),
        ):
            c = self.config.get(key, "#000000")
            btn.setText(f"{label}: {c}")
            light = self._is_light(c)
            btn.setStyleSheet(
                f"background-color: {c}; "
                f"color: {'#000000' if light else '#FFFFFF'}; font-weight: bold;"
            )

    @staticmethod
    def _is_light(hex_color: str) -> bool:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (r * 299 + g * 587 + b * 114) / 1000 > 128

    def _collect_settings(self):
        """Текущие значения всех настроек (кроме блока шифрования) — для
        сравнения с исходным снимком при закрытии."""
        return {
            "font": self.font_combo.currentText(),
            "font_size": int(self.font_size_combo.currentText()),
            "selected_theme": self.theme_combo.currentText(),
            "remember_geometry": self.remember_geo_check.isChecked(),
            "screenshot_protect": self.ss_protect_check.isChecked(),
            "idle_lock_mins": int(self.idle_mins.text() or "0"),
            "backup_folder": self.backup_folder_edit.text().strip(),
            "backup_auto_on_close": self.backup_auto_check.isChecked(),
            "backup_keep_count": self.backup_keep_spin.value(),
            "warn_on_exit_unsaved": self.warn_exit_check.isChecked(),
            "clipboard_clear_secs": int(self.clip_clear_secs.text() or "0"),
            "clipboard_clear_on_exit": self.clip_clear_exit_check.isChecked(),
            "recycle_bin_enabled": self.recycle_bin_check.isChecked(),
            "text_color": self.config.get("text_color"),
            "tree_bg_color": self.config.get("tree_bg_color"),
            "main_bg_color": self.config.get("main_bg_color"),
            "shortcuts": dict(getattr(self, "_sc_working", {})),
        }

    def _has_unsaved_changes(self):
        if not hasattr(self, "_initial_settings"):
            return False
        return self._collect_settings() != self._initial_settings

    def _ask_close_action(self):
        """Диалог при закрытии с несохранёнными изменениями.
        Возвращает 'apply' | 'discard' | 'return'."""
        d = ThemedDialog(self.config, self)
        d.setWindowTitle("Закрыть без применения изменений?")
        d.setMinimumWidth(420)
        lay = d.body
        lay.addWidget(QLabel("В настройках есть несохранённые изменения."))
        result = {"action": "return"}
        row = QHBoxLayout()
        apply_btn = QPushButton("Применить и закрыть"); apply_btn.setDefault(True)
        close_btn = QPushButton("Закрыть")
        back_btn = QPushButton("Вернуться")

        def choose(a):
            result["action"] = a
            d.accept()
        apply_btn.clicked.connect(lambda: choose("apply"))
        close_btn.clicked.connect(lambda: choose("discard"))
        back_btn.clicked.connect(lambda: choose("return"))
        row.addWidget(apply_btn); row.addWidget(close_btn); row.addWidget(back_btn)
        lay.addLayout(row)
        d.exec()
        return result["action"]

    def reject(self):
        if self._has_unsaved_changes():
            action = self._ask_close_action()
            if action == "return":
                return                    # остаёмся в настройках, ничего не теряя
            if action == "apply":
                self._apply_settings()    # применяет и закрывает
                return
            # action == "discard": продолжаем — закрыть без сохранения
        # Откат цветов/шрифта и живого предпросмотра при отмене / закрытии
        # без сохранения.
        for key, val in self._orig_colors.items():
            self.config.set(key, val)
        self.config.set("font", self._orig_font)
        self.config.set("font_size", self._orig_font_size)
        self.setStyleSheet(dialog_stylesheet(self.config))
        self._restyle_shortcuts()
        self.appearance_changed.emit()
        super().reject()

    def _apply_theme(self, name: str):
        if name in RETRO_THEMES:
            t = RETRO_THEMES[name]
            self.config.set("text_color",    t["text"])
            self.config.set("tree_bg_color", t["tree_bg"])
            self.config.set("main_bg_color", t["main_bg"])
            self.setStyleSheet(dialog_stylesheet(self.config))
            self._refresh_color_btns()
            self._restyle_shortcuts()
            self.appearance_changed.emit()  # живой предпросмотр в главном окне

    def _apply_font_preview(self, *_):
        """Живой предпросмотр шрифта и кегля — и в окне настроек, и в главном
        окне (по аналогии с темой)."""
        self.config.set("font", self.font_combo.currentText())
        self.config.set("font_size", int(self.font_size_combo.currentText() or "13"))
        self._apply_group_fonts()
        self.setStyleSheet(dialog_stylesheet(self.config))
        self._restyle_shortcuts()
        self.appearance_changed.emit()

    def _choose_color(self, key: str):
        current = self.config.get(key, "#000000")
        color = QColorDialog.getColor(current, self)
        if color.isValid():
            self.config.set(key, color.name())
            self._refresh_color_btns()
            self._restyle_shortcuts()

    def _browse_backup_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Папка для бэкапов", self.backup_folder_edit.text()
        )
        if folder:
            self.backup_folder_edit.setText(folder)

    def _refresh_backup_list(self):
        if not hasattr(self, "backup_list"):
            return
        self.backup_list.clear()
        folder = self.backup_folder_edit.text().strip()
        for p in bk.list_backups(folder):
            item = QListWidgetItem(p.name)
            item.setData(Qt.UserRole, str(p))
            self.backup_list.addItem(item)

    def _do_backup(self):
        folder = self.backup_folder_edit.text().strip()
        if not folder:
            themed_info(self.config, self, "Ошибка", "Укажите папку для бэкапов.")
            return
        # Бэкап читает файл с диска. Через гейт: в шифр. режиме отложенный снимок
        # сбрасывается и воркер квисцируется ДО чтения файла, поэтому копия
        # содержит свежие данные и не конкурирует с фоновой записью (H3-01).
        ok, res = self._run_vault_op(
            lambda: bk.create_backup(self._db_path, folder, self.backup_keep_spin.value()))
        if not ok:
            themed_info(self.config, self, "Ошибка", f"Не удалось создать бэкап:\n{res}")
            return
        self._refresh_backup_list()
        themed_info(self.config, self, "Бэкап создан", f"Файл сохранён:\n{res.name}")

    def _do_restore(self):
        item = self.backup_list.currentItem()
        if not item:
            themed_info(self.config, self, "Ошибка", "Выберите бэкап из списка.")
            return
        path = item.data(Qt.UserRole)
        from pathlib import Path as _P
        if not themed_confirm(
            self.config, self, "Восстановление",
            f"Восстановить из:\n{_P(path).name}\n\nТекущие данные будут заменены. Продолжить?"
        ):
            return
        # Всю последовательность ведёт MainWindow: он закрывает SQLite ДО замены
        # файла (иначе на Windows os.replace падает с WinError 5 в plaintext —
        # Баг 1), заменяет файл и переоткрывает БД. Диалог лишь показывает итог.
        ok, err = self._restore_handler(path)
        if not ok:
            themed_info(self.config, self, "Ошибка", f"Не удалось восстановить:\n{err}")
            return
        self._restore_done = True
        themed_info(self.config, self, "Восстановление",
                    "База данных восстановлена из бэкапа.")

    def _delete_all(self):
        if not themed_confirm(
            self.config, self, "Удаление всех данных",
            "Удалить ВСЕ аккаунты, папки, сервисы и вложения?\n\nЭто действие необратимо."
        ):
            return
        if not themed_confirm(
            self.config, self, "Подтверждение",
            "Последнее предупреждение.\nВсе данные будут стёрты безвозвратно. Продолжить?"
        ):
            return
        self._delete_all_confirmed = True
        self.accept()

    def _apply_settings(self):
        self.config.set("font",              self.font_combo.currentText())
        self.config.set("font_size",         int(self.font_size_combo.currentText()))
        self.config.set("selected_theme",    self.theme_combo.currentText())
        self.config.set("remember_geometry", self.remember_geo_check.isChecked())
        self.config.set("screenshot_protect",  self.ss_protect_check.isChecked())
        self.config.set("idle_lock_mins",      int(self.idle_mins.text() or "0"))
        self.config.set("backup_folder",         self.backup_folder_edit.text().strip())
        self.config.set("backup_auto_on_close",  self.backup_auto_check.isChecked())
        self.config.set("backup_keep_count",     self.backup_keep_spin.value())
        self.config.set("warn_on_exit_unsaved",  self.warn_exit_check.isChecked())
        self.config.set("clipboard_clear_secs",    int(self.clip_clear_secs.text() or "0"))
        self.config.set("clipboard_clear_on_exit", self.clip_clear_exit_check.isChecked())
        self.config.set("recycle_bin_enabled",     self.recycle_bin_check.isChecked())
        self.config.set("image_downscale",         self.image_downscale_check.isChecked())
        # Шорткаты: в конфиг кладём только отличия от дефолтов (компактно и
        # forward-compatible — новые действия унаследуют дефолт).
        self.config.set("shortcuts", {
            sid: seq for sid, seq in self._sc_working.items()
            if seq != shortcuts.DEFAULTS.get(sid)
        })
        self.config.save()
        self.settings_applied.emit()
        self.accept()


class KeyCaptureDialog(ThemedDialog):
    """Модальное окно захвата сочетания клавиш. Ловит реальное нажатие через
    keyPressEvent: Esc — отмена, Backspace — снять сочетание. При конфликте с
    другим действием назначение блокируется и показывается предупреждение."""

    _MOD_MASK = (Qt.ControlModifier | Qt.ShiftModifier
                 | Qt.AltModifier | Qt.MetaModifier)
    _MODIFIER_KEYS = {Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta,
                      Qt.Key_AltGr, Qt.Key_CapsLock, Qt.Key_NumLock,
                      Qt.Key_ScrollLock}

    def __init__(self, config, working, sid, parent=None):
        super().__init__(config, parent)
        self._working = working
        self._sid = sid
        self.result_sequence = working.get(sid, "")
        self.setWindowTitle("Назначение клавиши")
        self.setModal(True)
        self.setMinimumWidth(420)

        lay = self.body
        lay.addWidget(QLabel(f"Действие: {shortcuts.LABELS.get(sid, sid)}"))
        self._prompt = QLabel("Нажмите сочетание клавиш…\n"
                              "Esc — отмена, Backspace — снять сочетание.")
        self._prompt.setWordWrap(True)
        lay.addWidget(self._prompt)

    @staticmethod
    def _norm(seq):
        return QKeySequence(seq).toString() if seq else ""

    def keyPressEvent(self, e):
        key = e.key()
        mods = e.modifiers() & self._MOD_MASK
        if key == Qt.Key_Escape and mods == Qt.NoModifier:
            self.reject()
            return
        if key == Qt.Key_Backspace and mods == Qt.NoModifier:
            self.result_sequence = ""
            self.accept()
            return
        if key in self._MODIFIER_KEYS:
            return                       # одиночный модификатор — ждём дальше
        # Буквы/цифры записываем латиницей по физической клавише
        # (nativeVirtualKey не зависит от раскладки: VK A–Z = 0x41–0x5A,
        # 0–9 = 0x30–0x39). Для остальных клавиш (F1, Del…) берём e.key().
        vk = e.nativeVirtualKey()
        key_code = vk if (0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39) else key
        candidate = QKeySequence(int(mods.value) | int(key_code)).toString()
        if not candidate:
            return
        # Конфликт: блокируем и подсвечиваем
        for other_sid, other_seq in self._working.items():
            if other_sid != self._sid and self._norm(other_seq) == candidate:
                self._prompt.setText(
                    f"Сочетание «{candidate}» уже назначено действию "
                    f"«{shortcuts.LABELS.get(other_sid, other_sid)}». "
                    f"Выберите другое.")
                self._prompt.setStyleSheet("color: #C0392B; font-weight: bold;")
                return
        self.result_sequence = candidate
        self.accept()


class RecycleBinDialog(ThemedDialog):
    """Корзина: список удалённых аккаунтов с восстановлением и безвозвратным
    удалением. self.changed = True, если что-то восстановили/удалили (тогда
    главное окно перестроит дерево и обновит кнопку корзины)."""

    def __init__(self, config, db, parent=None):
        super().__init__(config, parent)
        self._db = db
        self.changed = False
        self.setWindowTitle("Корзина")
        self.setModal(True)
        self.setMinimumSize(460, 360)

        lay = self.body
        lay.addWidget(QLabel("Удалённые аккаунты:"))
        self._list = QListWidget()
        lay.addWidget(self._list, 1)

        self._empty_label = QLabel("Корзина пуста.")
        self._empty_label.setWordWrap(True)
        lay.addWidget(self._empty_label)

        row = QHBoxLayout()
        self._restore_btn = QPushButton("Восстановить")
        self._restore_btn.clicked.connect(self._restore)
        self._del_btn = QPushButton("Удалить навсегда")
        self._del_btn.clicked.connect(self._delete_forever)
        self._empty_btn = QPushButton("Очистить корзину")
        self._empty_btn.clicked.connect(self._empty)
        row.addWidget(self._restore_btn)
        row.addWidget(self._del_btn)
        row.addWidget(self._empty_btn)
        lay.addLayout(row)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        lay.addLayout(close_row)

        self._refresh()

    def _refresh(self):
        self._list.clear()
        rows = self._db.get_deleted_accounts()
        for r in rows:
            stamp = str(r["deleted_at"] or "")[:19]
            label = r["name"] if not stamp else f"{r['name']}   (удалён: {stamp})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, r["id"])
            self._list.addItem(item)
        has_items = bool(rows)
        self._empty_label.setVisible(not has_items)
        self._list.setVisible(has_items)
        for b in (self._restore_btn, self._del_btn, self._empty_btn):
            b.setEnabled(has_items)
        if has_items:
            self._list.setCurrentRow(0)

    def _current_id(self):
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _restore(self):
        aid = self._current_id()
        if aid is None:
            return
        self._db.restore_account(aid)
        self.changed = True
        self._refresh()

    def _delete_forever(self):
        aid = self._current_id()
        if aid is None:
            return
        if not themed_confirm(self.config, self, "Удаление",
                              "Удалить аккаунт безвозвратно?\nЭто действие необратимо."):
            return
        self._db.delete_account(aid)
        self.changed = True
        self._refresh()

    def _empty(self):
        if self._db.get_deleted_count() == 0:
            return
        if not themed_confirm(self.config, self, "Очистка корзины",
                              "Безвозвратно удалить ВСЕ аккаунты из корзины?\n"
                              "Это действие необратимо."):
            return
        self._db.empty_bin()
        self.changed = True
        self._refresh()


class UnlockDialog(ThemedDialog):
    """Окно ввода мастер-пароля при запуске / после автоблокировки.

    При успехе self.result_data = (db_bytes, dek, header). При отмене (выход)
    результат остаётся None — вызывающий код должен завершить программу."""

    def __init__(self, config, container: bytes, parent=None):
        super().__init__(config, parent)
        self.setWindowTitle("Разблокировка")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._container = container
        self.result_data = None
        self.recovery_action = None   # None | "reset" | "restore"
        self.restore_path = None

        lay = self.body
        lay.addWidget(QLabel("Введите мастер-пароль для доступа к базе:"))
        field_row = QHBoxLayout()
        self._field = QLineEdit()
        self._field.setEchoMode(QLineEdit.Password)
        field_row.addWidget(self._field, 1)
        paste_btn = QPushButton("Вставить")
        paste_btn.setToolTip("Вставить из буфера обмена")
        paste_btn.clicked.connect(self._paste)
        field_row.addWidget(paste_btn)
        lay.addLayout(field_row)

        self._rec_check = QCheckBox("Использовать recovery-код вместо пароля")
        self._rec_check.toggled.connect(self._on_mode_toggle)
        lay.addWidget(self._rec_check)

        self._err = QLabel("")
        self._err.setWordWrap(True)
        lay.addWidget(self._err)

        forgot_btn = QPushButton("Забыли пароль и recovery-код?")
        forgot_btn.clicked.connect(self._forgot)
        forgot_row = QHBoxLayout()
        forgot_row.addWidget(forgot_btn)
        forgot_row.addStretch()
        lay.addLayout(forgot_row)

        row = QHBoxLayout()
        row.addStretch()
        self._ok = QPushButton("Разблокировать")
        self._ok.setDefault(True)
        self._ok.clicked.connect(self._attempt)
        exit_btn = QPushButton("Выход")
        exit_btn.clicked.connect(self.reject)
        row.addWidget(self._ok)
        row.addWidget(exit_btn)
        lay.addLayout(row)

        self._field.returnPressed.connect(self._attempt)
        QTimer.singleShot(0, self._field.setFocus)

    def _paste(self):
        self._field.setText(QApplication.clipboard().text().strip())
        self._err.setText("")

    def _forgot(self):
        """Меню действий, если утеряны и пароль, и recovery-код. Расшифровать
        текущие данные невозможно — можно восстановить бэкап или начать заново."""
        d = ThemedDialog(self.config, self)
        d.setWindowTitle("Доступ к данным утерян")
        d.setMinimumWidth(460)
        lay = d.body
        lay.addWidget(QLabel(
            "Без мастер-пароля или recovery-кода расшифровать текущие данные\n"
            "НЕВОЗМОЖНО. Текущий зашифрованный файл будет сохранён рядом\n"
            "(переименован) на случай, если код вспомнится.\n\n"
            "Выберите, как продолжить:"))
        restore_btn = QPushButton("Восстановить из бэкапа…")
        reset_btn = QPushButton("Начать с чистой базы")
        cancel_btn = QPushButton("Отмена")
        lay.addWidget(restore_btn)
        lay.addWidget(reset_btn)
        rr = QHBoxLayout(); rr.addStretch(); rr.addWidget(cancel_btn)
        lay.addLayout(rr)

        def do_restore():
            path = self._pick_backup(d)
            if not path:
                return
            if not themed_confirm(
                self.config, d, "Восстановление из бэкапа",
                "Текущие зашифрованные данные станут недоступны "
                "(файл будет отложен). Восстановить выбранный бэкап?"):
                return
            self.recovery_action = "restore"
            self.restore_path = path
            d.accept()
            self.accept()

        def do_reset():
            if not themed_confirm(
                self.config, d, "Начать с чистой базы",
                "Будет создана новая пустая база. Доступ к текущим "
                "зашифрованным данным будет утерян. Продолжить?"):
                return
            self.recovery_action = "reset"
            d.accept()
            self.accept()

        restore_btn.clicked.connect(do_restore)
        reset_btn.clicked.connect(do_reset)
        cancel_btn.clicked.connect(d.reject)
        d.exec()

    @staticmethod
    def _browse_backup_file(parent):
        path, _ = QFileDialog.getOpenFileName(
            parent, "Выберите файл бэкапа", "",
            "База Хранилки (*.db);;Все файлы (*)")
        return path or None

    def _pick_backup(self, parent):
        """Предложить бэкапы из настроенной папки; если папка не задана или
        пуста — открыть проводник. Возвращает путь к файлу или None."""
        folder = self.config.get("backup_folder", "").strip()
        backups = bk.list_backups(folder) if folder else []
        if not backups:
            return self._browse_backup_file(parent)

        d = ThemedDialog(self.config, parent)
        d.setWindowTitle("Выбор бэкапа")
        d.setMinimumSize(440, 320)
        lay = d.body
        lay.addWidget(QLabel(f"Бэкапы из папки:\n{folder}"))
        lst = QListWidget()
        for p in backups:
            item = QListWidgetItem(p.name)
            item.setData(Qt.UserRole, str(p))
            lst.addItem(item)
        lst.setCurrentRow(0)
        lst.itemDoubleClicked.connect(lambda *_: d.accept())
        lay.addWidget(lst, 1)

        chosen = {"path": None}
        row = QHBoxLayout()
        browse_btn = QPushButton("Другой файл…")
        row.addWidget(browse_btn)
        row.addStretch()
        ok = QPushButton("Выбрать"); ok.setDefault(True); ok.clicked.connect(d.accept)
        cancel = QPushButton("Отмена"); cancel.clicked.connect(d.reject)
        row.addWidget(ok); row.addWidget(cancel)
        lay.addLayout(row)

        def do_browse():
            p = self._browse_backup_file(d)
            if p:
                chosen["path"] = p
                d.accept()
        browse_btn.clicked.connect(do_browse)

        if d.exec() != QDialog.Accepted:
            return None
        if chosen["path"]:
            return chosen["path"]
        item = lst.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _on_mode_toggle(self, use_recovery: bool):
        if use_recovery:
            self._field.setEchoMode(QLineEdit.Normal)
            self._field.setPlaceholderText("XXXX-XXXX-XXXX-…")
        else:
            self._field.setEchoMode(QLineEdit.Password)
            self._field.setPlaceholderText("")
        self._field.clear()
        self._err.setText("")

    def _attempt(self):
        secret = self._field.text().strip()
        if not secret:
            return
        is_rec = self._rec_check.isChecked()
        try:
            self.result_data = cs.unlock(self._container, secret, is_recovery=is_rec)
            self.accept()
        except cs.WrongPassword:
            self._err.setText("Неверный пароль или recovery-код.")
            self._field.selectAll()
            self._field.setFocus()
        except cs.CorruptVault as e:
            self._err.setText(f"Файл базы повреждён: {e}")


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
        warn.setStyleSheet(f"color: {self.config.get('text_color', '#000000')}; "
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
