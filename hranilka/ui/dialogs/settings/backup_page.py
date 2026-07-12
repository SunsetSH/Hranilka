"""Вкладка «Бэкапы»: папка, авто-бэкап при закрытии, ротация.
Часть SettingsDialog (dialog.py) — методы вынесены дословно (backlog-разрез по страницам)."""
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QPushButton, QLabel, QCheckBox, QSpinBox, QLineEdit, QListWidget, QWidget)

# Прямой импорт из модуля диалога (не из пакета hranilka.ui.dialogs — тот
# реэкспортирует SettingsDialog, собираемый из этого миксина: цикл импорта).
# Фикс NameError: раньше имя ExportDialog использовалось без импорта.
from hranilka.ui.dialogs.export_dialog import ExportDialog


class SettingsBackupPageMixin:
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
        dlg = ExportDialog(self.config, tree, "Вся база", self,
                           show_fin=self.config.get("show_fin_instruments", False))
        dlg.exec()

