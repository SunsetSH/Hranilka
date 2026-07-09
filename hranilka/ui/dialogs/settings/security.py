"""Вкладки «Безопасность» и блок «Шифрование»: авто-блокировка, защита
от скриншотов, включение/выключение шифрования, смена мастер-пароля,
recovery-код (привилегированные операции идут через vault-гейт).
Часть SettingsDialog (dialog.py) — методы вынесены дословно (backlog-разрез по страницам)."""
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QComboBox, QPushButton, QLabel, QCheckBox, QLineEdit, QFileDialog, QWidget, QDialog, QApplication)
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIntValidator
from hranilka.ui.theme import themed_confirm
from hranilka.ui.theme import themed_info
from hranilka.ui.theme import ThemedDialog
from hranilka.ui.dialogs.recovery import RecoveryCodeDialog
from hranilka.core import util


class SettingsSecurityMixin:
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
        idle_note = QLabel(
            "Пока в аккаунте есть несохранённые изменения, автоблокировка "
            "откладывается (чтобы не потерять правки) — по простою она не "
            "сработает до сохранения или отмены. В это время данные остаются "
            "расшифрованными в памяти дольше заданного времени, поэтому, отходя "
            "от компьютера, сохраняйте или отменяйте редактирование.")
        idle_note.setWordWrap(True)
        ilf.addRow(idle_note)
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

    def _ask_credential(self, title: str, prompt: str, with_recovery: bool):
        """Общий однопольный ввод секрета (L-11): каркас диалога один, а режим
        задаётся параметром. При with_recovery добавляются переключатель
        «использовать recovery-код» и вставка из буфера.

        Возвращает None при отмене; иначе (secret, is_recovery) при
        with_recovery=True либо просто строку при with_recovery=False."""
        d = ThemedDialog(self.config, self)
        d.setWindowTitle(title)
        d.setMinimumWidth(400 if with_recovery else 380)
        lay = d.body
        lay.addWidget(QLabel(prompt))
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.Password)
        lay.addWidget(edit)

        rec_check = None
        if with_recovery:
            rec_check = QCheckBox("Использовать recovery-код")

            def on_toggle(use_rec):
                edit.setEchoMode(QLineEdit.Normal if use_rec else QLineEdit.Password)
                edit.setPlaceholderText("XXXX-XXXX-…" if use_rec else "")
                edit.clear()
            rec_check.toggled.connect(on_toggle)
            lay.addWidget(rec_check)

            paste_row = QHBoxLayout()
            paste_btn = QPushButton("Вставить из буфера")
            paste_btn.clicked.connect(
                lambda: edit.setText(QApplication.clipboard().text()))
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
        if with_recovery:
            return edit.text().strip(), rec_check.isChecked()
        return edit.text()

    def _ask_secret(self, title: str, prompt: str):
        """Ввод мастер-пароля ИЛИ recovery-кода. Возвращает (secret, is_recovery)
        или None (отмена). Есть переключатель режима и вставка из буфера."""
        return self._ask_credential(title, prompt, with_recovery=True)

    def _ask_password(self, title: str, prompt: str):
        """Однопольный ввод пароля. Возвращает строку или None (отмена)."""
        return self._ask_credential(title, prompt, with_recovery=False)

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
        """Показать recovery-код один раз: копировать / сохранить в файл.
        Закрытие без «Я сохранил код» переспрашивает (L-3)."""
        from PySide6.QtWidgets import QApplication
        d = RecoveryCodeDialog(self.config, self)
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

