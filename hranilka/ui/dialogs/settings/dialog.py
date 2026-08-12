"""SettingsDialog — ядро диалога настроек: сборка вкладок из
страниц-миксинов (по модулю на вкладку), кнопки «Применить/Отмена»,
сбор и применение значений, снимок настроек для определения
несохранённых изменений при закрытии."""
from PySide6.QtWidgets import (QHBoxLayout, QGroupBox, QPushButton, QLabel, QListWidgetItem, QFileDialog, QFrame, QColorDialog)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont
from hranilka.core.config import RETRO_THEMES
from hranilka.core.paths import BASE_DIR
from hranilka.ui.theme import themed_confirm
from hranilka.ui.theme import themed_info
from hranilka.ui.theme import dialog_stylesheet
from hranilka.ui.theme import ThemedDialog
from hranilka.ui.flowlayout import WrappingTabWidget
from hranilka.services import backup as bk
from hranilka.core import shortcuts
from hranilka.ui.theme import ThemedDialog

from hranilka.ui.dialogs.settings.appearance import SettingsAppearanceMixin
from hranilka.ui.dialogs.settings.backup_page import SettingsBackupPageMixin
from hranilka.ui.dialogs.settings.behavior import SettingsBehaviorMixin
from hranilka.ui.dialogs.settings.data_page import SettingsDataPageMixin
from hranilka.ui.dialogs.settings.options import SettingsOptionsMixin
from hranilka.ui.dialogs.settings.security import SettingsSecurityMixin
from hranilka.ui.dialogs.settings.shortcuts_page import SettingsShortcutsMixin


class SettingsDialog(SettingsAppearanceMixin, SettingsSecurityMixin,
                     SettingsBackupPageMixin, SettingsDataPageMixin,
                     SettingsOptionsMixin, SettingsBehaviorMixin,
                     SettingsShortcutsMixin, ThemedDialog):
    settings_applied = Signal()      # финальное «Применить» (полная переинициализация)
    appearance_changed = Signal()    # живой предпросмотр шрифта/темы/цвета (только стили)

    def __init__(self, config, parent=None):
        super().__init__(config, parent)
        self.setWindowTitle("Настройки")
        self.setModal(True)
        # Ширину задаём по одному ряду вкладок (см. _fit_width_to_tabs после
        # сборки UI); высоту не форсируем — реальный минимум задаёт самая
        # высокая вкладка.
        self.setMinimumHeight(400)
        # Дефолт — БД рядом с программой (как в main), а не относительно cwd;
        # главное окно всё равно переопределяет путь через set_db_path.
        self._db_path = str(BASE_DIR / "hranilka.db")
        self._db = None
        self._run_vault_op = self._default_vault_op
        self._run_vault_op_heavy = None   # KDF-тяжёлый runner; None → _run_vault_op
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

    @property
    def delete_all_confirmed(self) -> bool:
        """Подтвердил ли пользователь «УДАЛИТЬ ВСЕ ДАННЫЕ» (двойное
        подтверждение на вкладке «Данные»). Публичное read-only свойство для
        главного окна (L-10) — приватный флаг наружу не отдаём."""
        return self._delete_all_confirmed

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

    def set_vault_runner_heavy(self, runner):
        """Внедрить runner для KDF-тяжёлых операций (вкл/выкл шифрования, смена
        пароля, recovery-код): тот же гейт, но fn выполняется в фоновом потоке
        с модальным progress — GUI не подвисает (H-09).
        runner(fn, message) -> (ok, result_or_error)."""
        self._run_vault_op_heavy = runner

    def _vault_op_heavy(self, fn, message: str):
        """KDF-тяжёлая привилегированная операция: через heavy-runner, если
        внедрён; иначе — обычный синхронный гейт (диалог вне главного окна)."""
        if self._run_vault_op_heavy is not None:
            return self._run_vault_op_heavy(fn, message)
        return self._run_vault_op(fn)

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
        self._tabs.addTab(self._page_options(),    "Опции")
        self._tabs.addTab(self._page_behavior(),   "Поведение")
        self._tabs.addTab(self._page_shortcuts(),  "Шорткаты")
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
        # Шрифт QGroupBox наследуется вложенными полями и меняет их sizeHint.
        # Обновляем защитный минимум после финального применения шрифтов, иначе
        # поле очистки буфера сохраняет высоту, рассчитанную до стилизации.
        self.clip_clear_secs.setMinimumHeight(
            self.clip_clear_secs.sizeHint().height())
        self._fit_width_to_tabs()
        # Снимок значений всех настроек для определения несохранённых изменений
        # при закрытии (блок шифрования сюда не входит — он применяется сразу).
        self._initial_settings = self._collect_settings()

    def _fit_width_to_tabs(self):
        """Сузить окно до ширины одного ряда вкладок: ширина панели вкладок +
        поля тела диалога (14+14) + рамка dialogFrame (2+2)."""
        width = self._tabs.one_row_width() + 14 + 14 + 2 + 2
        self.setMinimumWidth(width)
        self.resize(width, max(400, self.height()))

    def _apply_group_fonts(self):
        """Шрифт заголовков групп через стили Qt применяет ненадёжно, поэтому
        задаём его виджетам QGroupBox напрямую: выбранный шрифт, кегль −2."""
        font_name = self.config.get("font", "Cascadia Code")
        font_size = self.config.get("font_size", 14)
        gf = QFont(font_name, max(1, font_size - 2))
        for gb in self.findChildren(QGroupBox):
            gb.setFont(gf)


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
            "hide_empty_card_fields":
                self.hide_empty_card_fields_check.isChecked(),
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
            "show_fin_instruments": self.show_fin_check.isChecked(),
            "show_servers": self.show_servers_check.isChecked(),
            "gallery_thumb_preload": self._thumb_preload_value(),
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
        # Бэкап читает файл с диска. Через гейт: очередь run_async и воркер
        # квисцируются, свежий снимок сброшен ДО чтения файла — копия актуальна
        # и не конкурирует с фоновой записью (H3-01). Heavy-runner: копирование
        # и fsync большой базы идут в фоне с progress, GUI не подвисает.
        # Значения Qt-виджетов снимаем ДО запуска: лямбда выполняется в фоновом
        # потоке, а обращаться к виджетам можно только из GUI-потока.
        db_path = self._db_path
        keep_count = self.backup_keep_spin.value()
        ok, res = self._vault_op_heavy(
            lambda: bk.create_backup(db_path, folder, keep_count),
            "Создание бэкапа: копирование базы…")
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

    def _resolve_show_fin(self) -> bool:
        """Значение show_fin_instruments для применения. При переходе True→False
        с существующими фин-записями (включая корзину) — themed-подтверждение;
        отказ возвращает чекбокс в True (остальные настройки применяются)."""
        new_val = self.show_fin_check.isChecked()
        old_val = self.config.get("show_fin_instruments", False)
        if (old_val and not new_val and self._db is not None
                and self._db.count_fin_items() > 0):
            if not themed_confirm(
                    self.config, self, "Скрыть финансовые инструменты",
                    "Фин-инструменты будут скрыты из интерфейса (дерево, связи, "
                    "экспорт, создание). Записи останутся в БД, корзина продолжит "
                    "их показывать. Несохранённые правки фин-записей будут "
                    "сброшены. Продолжить?"):
                self.show_fin_check.setChecked(True)
                return True
        return new_val

    def _resolve_show_servers(self) -> bool:
        """Значение show_servers для применения. При переходе True→False с
        существующими серверами (включая корзину) — themed-подтверждение;
        отказ возвращает чекбокс в True (остальные настройки применяются)."""
        new_val = self.show_servers_check.isChecked()
        old_val = self.config.get("show_servers", False)
        if (old_val and not new_val and self._db is not None
                and self._db.count_servers() > 0):
            if not themed_confirm(
                    self.config, self, "Скрыть серверы",
                    "Серверы будут скрыты из интерфейса (дерево, связи, "
                    "создание). Записи останутся в БД, корзина продолжит их "
                    "показывать. Несохранённые правки серверов будут "
                    "сброшены. Продолжить?"):
                self.show_servers_check.setChecked(True)
                return True
        return new_val

    def _apply_settings(self):
        self.config.set("font",              self.font_combo.currentText())
        self.config.set("font_size",         int(self.font_size_combo.currentText()))
        self.config.set("selected_theme",    self.theme_combo.currentText())
        self.config.set("hide_empty_card_fields",
                        self.hide_empty_card_fields_check.isChecked())
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
        # show_fin_instruments / show_servers — с предупреждением при переходе
        # True→False с существующими записями (до записи нового значения).
        self.config.set("show_fin_instruments",    self._resolve_show_fin())
        self.config.set("show_servers",             self._resolve_show_servers())
        self.config.set("image_downscale",         self.image_downscale_check.isChecked())
        self.config.set("gallery_thumb_preload",   self._thumb_preload_value())
        # Шорткаты: в конфиг кладём только отличия от дефолтов (компактно и
        # forward-compatible — новые действия унаследуют дефолт).
        self.config.set("shortcuts", {
            sid: seq for sid, seq in self._sc_working.items()
            if seq != shortcuts.DEFAULTS.get(sid)
        })
        self.config.save()
        self.settings_applied.emit()
        self.accept()

