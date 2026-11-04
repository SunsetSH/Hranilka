"""Вкладка «Опции»: корзина, показ финансовых инструментов, обновления.
Часть SettingsDialog (dialog.py) — страница-миксин (по модулю на вкладку)."""
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
                               QCheckBox, QPushButton, QWidget)

from hranilka import __version__


class SettingsOptionsMixin:
    # ─── Вкладка: Опции ──────────────────────────────────────────────────────

    def _page_options(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.setContentsMargins(0, 8, 0, 0)

        bin_group = QGroupBox("Корзина")
        bl = QVBoxLayout(bin_group)
        self.recycle_bin_check = QCheckBox("Удалять в корзину")
        self.recycle_bin_check.setChecked(
            self.config.get("recycle_bin_enabled", False))
        bl.addWidget(self.recycle_bin_check)
        bin_note = QLabel(
            "Если включено, удалённые аккаунты перемещаются в корзину, откуда "
            "их можно восстановить. Кнопка корзины появляется рядом с "
            "«Настройки».\n"
            "Если выключено, аккаунты удаляются сразу и безвозвратно.")
        bin_note.setWordWrap(True)
        bl.addWidget(bin_note)
        lay.addWidget(bin_group)

        fin_group = QGroupBox("Финансовые инструменты")
        fl = QVBoxLayout(fin_group)
        self.show_fin_check = QCheckBox("Показывать фин. инструменты")
        self.show_fin_check.setChecked(
            self.config.get("show_fin_instruments", False))
        fl.addWidget(self.show_fin_check)
        fin_note = QLabel(
            "Карты и криптокошельки: показ в дереве, связях, экспорте и кнопках "
            "создания. Если выключено — записи скрыты из интерфейса, но остаются "
            "в базе; корзина продолжает их показывать.")
        fin_note.setWordWrap(True)
        fl.addWidget(fin_note)
        lay.addWidget(fin_group)

        srv_group = QGroupBox("VPS-серверы")
        sl = QVBoxLayout(srv_group)
        self.show_servers_check = QCheckBox("Показывать серверы")
        self.show_servers_check.setChecked(
            self.config.get("show_servers", False))
        sl.addWidget(self.show_servers_check)
        srv_note = QLabel(
            "Данные подключения к VPS-серверам: показ в дереве, связях и кнопках "
            "создания. Если выключено — записи скрыты из интерфейса, но остаются "
            "в базе; корзина продолжает их показывать.")
        srv_note.setWordWrap(True)
        sl.addWidget(srv_note)
        lay.addWidget(srv_group)

        lay.addWidget(self._build_updates_group())

        lay.addStretch()
        return w

    def _build_updates_group(self):
        group = QGroupBox("Обновления")
        gl = QVBoxLayout(group)
        self.check_updates_check = QCheckBox("Проверять обновления при запуске")
        self.check_updates_check.setChecked(self.config.get("check_updates", False))
        gl.addWidget(self.check_updates_check)
        note = QLabel(
            "При запуске программа запрашивает у GitHub номер последнего релиза "
            "и показывает окно, если вышла новая версия. Данные хранилища никуда "
            "не передаются.")
        note.setWordWrap(True)
        gl.addWidget(note)
        row = QHBoxLayout()
        row.addWidget(QLabel(f"Текущая версия: {__version__}"))
        row.addStretch()
        self.check_updates_btn = QPushButton("Проверить сейчас")
        self.check_updates_btn.clicked.connect(self._check_updates_now)
        row.addWidget(self.check_updates_btn)
        gl.addLayout(row)
        return group

    def _check_updates_now(self):
        if self._update_checker is None:
            return
        self.check_updates_btn.setEnabled(False)
        self._update_checker(
            self, lambda: self.check_updates_btn.setEnabled(True))
