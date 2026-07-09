"""Вкладка «Данные»: восстановление из бэкапа, сжатие БД, удаление
всех данных.
Часть SettingsDialog (dialog.py) — методы вынесены дословно (backlog-разрез по страницам)."""
from PySide6.QtWidgets import (QVBoxLayout, QGroupBox, QPushButton, QLabel, QCheckBox, QWidget)
from hranilka.ui.theme import themed_info


class SettingsDataPageMixin:
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
        # Намеренно жёсткие цвета (danger-стиль): пара фон+текст задана ВМЕСТЕ
        # (белый на тёмно-красном), поэтому самосогласована и читаема на любой
        # теме. Через настройки не выводится сознательно (H-10, допустимо).
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
        # фоновой записью. В шифр. режиме сразу же синхронно сбрасываем сжатый
        # контейнер на диск (flush) — чтобы «Готово» сообщалось по факту durable
        # записи, а не до неё (L6-02). Сбой записи вернётся как ошибка.
        def _vacuum_durable():
            self._db.vacuum()
            self._db.flush()        # no-op в обычном режиме; в шифр. — запись на диск
        ok, err = self._run_vault_op(_vacuum_durable)
        if not ok:
            themed_info(self.config, self, "Ошибка", f"Не удалось сжать базу:\n{err}")
            return
        themed_info(self.config, self, "Готово",
                    "База сжата: свободные страницы освобождены, файл уменьшен.")

