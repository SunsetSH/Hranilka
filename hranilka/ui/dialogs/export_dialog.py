"""Диалог экспорта в TXT/CSV/HTML/XLSX (вынесен из dialogs.py, этап 4).

M-04 (docs/CODE_REVIEW_VPS_SERVERS_2026-07-15.md): диалог открывается СРАЗУ,
без предварительного snapshot — параметры сначала, данные потом. Снимок БД
(отфильтрованный по выбранным галочкам — gallery BLOB/фин/серверы не читаются,
если выключены) и формирование файла идут в фоне (util.fire + db.run_async +
поток для CPU-тяжёлого форматирования), публикация — атомарным переименованием
(export.write_atomic). Диалог остаётся application-modal на время фоновой
работы (элементы управления блокированы) — тем же способом исключается
повторный параллельный экспорт."""
import asyncio
import logging

from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QGroupBox,
                               QHBoxLayout, QLabel, QPushButton, QRadioButton,
                               QVBoxLayout)

from hranilka.core import util
from hranilka.data.database import StaleSessionError
from hranilka.services import export
from hranilka.ui.theme import ThemedDialog, themed_info


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

    db — активная БД (снимок читается ПОСЛЕ подтверждения параметров, не при
    открытии диалога, M-04); node_type/node_id — что экспортировать (None,
    None — вся база, иначе ветка узла, как раньше принимал Database.
    export_subtree). title — что экспортируется (путь узла или «Вся база»);
    show_fin — показ фин-инструментов включён (опция настроек): при False
    чекбоксы фин-записей/секретов скрыты и записи в экспорт не попадают
    (include_fin=False жёстко). show_servers — зеркало show_fin для
    VPS-серверов (docs/ТЗ_VPS_Серверы.md §5), независимая ветка."""

    def __init__(self, config, db, node_type, node_id, title="Вся база", parent=None,
                 show_fin=True, show_servers=False, selected_nodes=None):
        super().__init__(config, parent)
        self.setWindowTitle("Экспорт")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._db = db
        self._node_type = node_type
        self._node_id = node_id
        self._selected_nodes = tuple(selected_nodes or ())
        self._title = title
        self._show_fin = show_fin
        self._show_servers = show_servers
        # Занятость: снимок+форматирование идут в фоне (util.fire); модальность
        # диалога уже не даёт открыть параллельно второй экспорт, а флаг —
        # защита от повторного клика «Экспортировать…» по тому же диалогу.
        self._busy = False

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

        # Финансовые инструменты (карты/кошельки) — отдельная группа. Все
        # чекбоксы «Что включить», включая секреты (CVV/PIN/seed/ключи), по
        # умолчанию ВКЛЮЧЕНЫ (УИ §2026-07-15); секреты активны, только пока
        # включены сами фин-записи (_on_fin_toggled). При show_fin=False оба
        # чекбокса скрыты и «Финансовые инструменты» снят — фин-записи в
        # экспорт не попадают (Options их жёстко исключает независимо от
        # состояния чекбокса секретов).
        self._chk_fin = QCheckBox("Финансовые инструменты")
        self._chk_fin.setChecked(show_fin)
        ol.addWidget(self._chk_fin)
        self._chk_fin_secrets = QCheckBox(
            "Включить критичные секреты (CVV, PIN, seed-фразы, приватные ключи)")
        self._chk_fin_secrets.setChecked(True)
        ol.addWidget(self._chk_fin_secrets)
        self._chk_fin.toggled.connect(self._on_fin_toggled)
        self._on_fin_toggled(self._chk_fin.isChecked())
        if not show_fin:
            self._chk_fin.setVisible(False)
            self._chk_fin_secrets.setVisible(False)

        # VPS-серверы — независимая от фин-инструментов ветка (зеркало пары
        # выше, docs/ТЗ_VPS_Серверы.md §5): секреты (пароли/приватные
        # SSH-ключи/passphrase) по умолчанию ВКЛЮЧЕНЫ (УИ §2026-07-15),
        # активны, только пока включены сами серверы (_on_servers_toggled).
        # При show_servers=False оба чекбокса скрыты и «VPS-серверы» снят —
        # серверы в экспорт не попадают.
        self._chk_servers = QCheckBox("VPS-серверы")
        self._chk_servers.setChecked(show_servers)
        ol.addWidget(self._chk_servers)
        self._chk_server_secrets = QCheckBox(
            "Включить секреты серверов (пароли, приватные SSH-ключи, passphrase)")
        self._chk_server_secrets.setChecked(True)
        ol.addWidget(self._chk_server_secrets)
        self._chk_servers.toggled.connect(self._on_servers_toggled)
        self._on_servers_toggled(self._chk_servers.isChecked())
        if not show_servers:
            self._chk_servers.setVisible(False)
            self._chk_server_secrets.setVisible(False)
        lay.addWidget(opt_group)

        warn = QLabel(
            "⚠ Экспорт сохраняет выбранные данные в ОТКРЫТОМ виде в обычный "
            "файл на диске. Храните файл в надёжном месте.")
        warn.setWordWrap(True)
        # Цвет — из темы (как у остального текста), но жирным для акцента.
        # Литеральный fallback убран (H-10): Config всегда содержит text_color,
        # а '#000000' не совпадал с фактическим дефолтом настроек.
        warn.setStyleSheet(f"color: {self.config.get('text_color')}; "
                           "font-weight: bold;")
        lay.addWidget(warn)

        # Подключаем после создания галочек: обработчик обращается к ним.
        self._fmt_btns.buttonToggled.connect(self._on_format_changed)
        self._on_format_changed()  # начальное состояние (галерея для TXT/CSV)

        # Занятость на время фонового снимка+форматирования (M-04): пустая
        # строка в покое, «Экспорт…» пока идёт фон.
        self._status = QLabel("")
        lay.addWidget(self._status)

        row = QHBoxLayout()
        row.addStretch()
        self._ok = QPushButton("Экспортировать…")
        self._ok.setDefault(True)
        self._ok.clicked.connect(self._do_export)
        self._cancel_btn = QPushButton("Отмена")
        self._cancel_btn.clicked.connect(self.reject)
        row.addWidget(self._ok)
        row.addWidget(self._cancel_btn)
        lay.addLayout(row)

    def _on_fin_toggled(self, checked):
        # Критичные секреты имеют смысл только при включённых фин-записях.
        self._chk_fin_secrets.setEnabled(checked)

    def _on_servers_toggled(self, checked):
        # Секреты серверов имеют смысл только при включённых серверах.
        self._chk_server_secrets.setEnabled(checked)

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
        if self._busy:
            return               # защита от повторного клика (M-04)
        fmt = self._current_format()
        func, ext, flt = export.FORMATS[fmt]
        if not (self._chk_basic.isChecked() or self._chk_other.isChecked()
                or (self._chk_gallery.isEnabled() and self._chk_gallery.isChecked())
                or (self._show_fin and self._chk_fin.isChecked())
                or (self._show_servers and self._chk_servers.isChecked())):
            themed_info(self.config, self, "Экспорт",
                        "Выберите хотя бы один пункт в разделе «Что включить».")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить экспорт", "hranilka_export" + ext, flt)
        if not path:
            return
        if not path.lower().endswith(ext):
            path += ext
        # Значения Qt-виджетов снимаем ДО запуска фона (образец — ручной бэкап,
        # settings/dialog.py:_do_backup): opts — обычный объект, фоновая
        # корутина к виджетам больше не обращается.
        opts = export.Options(
            include_basic=self._chk_basic.isChecked(),
            include_other=self._chk_other.isChecked(),
            include_gallery=self._chk_gallery.isEnabled() and self._chk_gallery.isChecked(),
            include_fin=self._show_fin and self._chk_fin.isChecked(),
            include_fin_secrets=(self._show_fin and self._chk_fin.isChecked()
                                 and self._chk_fin_secrets.isChecked()),
            include_servers=self._show_servers and self._chk_servers.isChecked(),
            include_server_secrets=(self._show_servers and self._chk_servers.isChecked()
                                    and self._chk_server_secrets.isChecked()),
            title=self._title,
            theme=theme_dict(self.config),
        )
        self._start_export(func, path, opts)

    def _start_export(self, func, path, opts):
        """Запускает фоновый снимок+форматирование (M-04). Диалог остаётся
        application-modal (setModal(True)+exec()) на время работы — элементы
        управления дополнительно блокируются, повторный клик по «Экспортировать…»
        того же диалога тоже отсекается флагом _busy."""
        self._busy = True
        self._set_controls_enabled(False)
        self._status.setText("Экспорт…")
        util.fire(self._run_export(func, path, opts))

    async def _run_export(self, func, path, opts):
        """Снимок БД — через db.run_async (фоновый поток, не блокирует UI),
        фильтрованный по включённым разделам (gallery BLOB и отключённые
        фин/серверы вовсе не читаются, bulk.py). Форматирование — CPU-тяжёлое
        и Qt-независимое (services/export.py) — в отдельном потоке
        (asyncio.to_thread), публикация — атомарным переименованием
        (export.write_atomic).

        HTML+галерея (M-04, второй проход ревью): снимок собирается БЕЗ BLOB
        (gallery_ids_only=True — только id строк галереи), а картинки читает
        по одной image_provider, переданный форматтеру через write_atomic;
        так пиковая память не растёт с объёмом галереи (docs/
        CODE_REVIEW_VPS_SERVERS_2026-07-15.md, M-04)."""
        session = self._db.current_session()
        stream_gallery = opts.include_gallery and func is export.export_html
        try:
            snapshot_method = (self._db.export_selected if self._selected_nodes
                               else self._db.export_subtree)
            snapshot_args = ((self._selected_nodes,) if self._selected_nodes
                             else (self._node_type, self._node_id))
            tree = await self._db.run_async(
                snapshot_method, *snapshot_args,
                include_gallery=opts.include_gallery,
                include_fin=opts.include_fin,
                include_servers=opts.include_servers,
                gallery_ids_only=stream_gallery,
                _session=session)
            if not tree:
                self._finish(empty=True)
                return
            extra = {}
            if stream_gallery:
                extra["image_provider"] = self._make_image_provider(session)
            await asyncio.to_thread(export.write_atomic, func, tree, opts, path, **extra)
        except StaleSessionError:
            # Сессия БД сменилась (lock/restore/close) во время экспорта —
            # обрываем без записи файла, без пользовательского сообщения (как
            # и другие фоновые операции проекта при устаревшей сессии).
            logging.info("Экспорт прерван: сессия БД изменилась во время операции.")
            self._finish(aborted=True)
            return
        except Exception as e:                        # noqa: BLE001 — фон, сообщаем пользователю
            logging.error("Ошибка экспорта: %s", e, exc_info=e)
            self._finish(error=str(e))
            return
        self._finish(path=path)

    def _make_image_provider(self, session):
        """provider(kind, image_id) -> bytes|None для потокового HTML (M-04,
        второй проход): читает БАЙТЫ ОДНОЙ картинки по запросу форматтера
        (services/export.py — вызывается изнутри asyncio.to_thread-потока
        форматирования, у которого нет своего event loop).

        Мост в БД — тот же db.run_async, что и у обычных карточек
        (run_coroutine_threadsafe планирует корутину на loop UI-потока и
        синхронно блокирует только поток форматирования до готовности):
        сохраняются оба инварианта Database — доступ к conn идёт из-под
        общего RLock, а не напрямую из чужого потока, и проверка сессии
        (_session=session) — смена сессии (lock/restore/close) во время
        экспорта поднимет тот же StaleSessionError, что и для snapshot,
        write_atomic прервётся и temp-файл будет убран (атомарность)."""
        loop = asyncio.get_running_loop()
        db = self._db
        methods = {"account": db.load_gallery_image,
                   "fin": db.load_fin_gallery_image,
                   "server": db.load_server_gallery_image}

        def provider(kind, image_id):
            fut = asyncio.run_coroutine_threadsafe(
                db.run_async(methods[kind], image_id, _session=session), loop)
            return fut.result()

        return provider

    def _finish(self, path=None, empty=False, aborted=False, error=None):
        self._busy = False
        self._set_controls_enabled(True)
        self._status.setText("")
        if error is not None:
            themed_info(self.config, self, "Ошибка экспорта",
                        f"Не удалось выполнить экспорт:\n{error}")
            return
        if aborted:
            return
        if empty:
            themed_info(self.config, self, "Экспорт", "Нечего экспортировать.")
            return
        themed_info(self.config, self, "Готово",
                    f"Экспортировано в файл:\n{path}")
        self.accept()

    def _set_controls_enabled(self, enabled):
        """Блокировка элементов управления на время фона (M-04)."""
        for btn in self._fmt_btns.buttons():
            btn.setEnabled(enabled)
        for c in (self._chk_basic, self._chk_other, self._chk_gallery,
                 self._chk_fin, self._chk_servers):
            c.setEnabled(enabled)
        self._ok.setEnabled(enabled)
        self._cancel_btn.setEnabled(enabled)
        if enabled:
            # Производные состояния (секреты зависят от fin/servers, галерея —
            # от формата) пересчитываем — простое setEnabled(True) выше
            # включило бы их безусловно.
            self._on_fin_toggled(self._chk_fin.isChecked())
            self._on_servers_toggled(self._chk_servers.isChecked())
            self._on_format_changed()
