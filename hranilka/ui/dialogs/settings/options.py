"""Вкладка «Опции»: корзина и показ финансовых инструментов.
Часть SettingsDialog (dialog.py) — страница-миксин (по модулю на вкладку)."""
from PySide6.QtWidgets import (QVBoxLayout, QGroupBox, QLabel, QCheckBox,
                               QWidget)


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

        lay.addStretch()
        return w
