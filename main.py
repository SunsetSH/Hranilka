import sys
import atexit
import logging
import ctypes
import ctypes.wintypes
from PySide6.QtWidgets import (QApplication, QMainWindow, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QLabel,
                               QVBoxLayout, QWidget, QHBoxLayout,
                               QPushButton, QMenu, QLineEdit,
                               QComboBox, QAbstractItemView)
from PySide6.QtCore import (Qt, Signal, Slot, QObject, QThread, QEventLoop,
                            QDate, QEvent, QTimer, QDateTime, QByteArray)
from PySide6.QtGui import QFont, QShortcut, QKeySequence, QColor, QBrush
import backup as bk

from config import Config
from database import Database, FutureSchemaError, VaultConflictError
import instance_lock
from paths import BASE_DIR
from models import AccountData
from dialogs import SettingsDialog, RecycleBinDialog, ExportDialog
from tabs import AccountTabs
from titlebar import TitleBar, ResizableContainer
import theme
import pd_generator
import util
import shortcuts


class AccountTree(QTreeWidget):
    """Дерево с ограниченным drag&drop (вариант A): перетаскиванием можно менять
    порядок только среди соседей ТОГО ЖЕ ТИПА и ТОГО ЖЕ родителя — включая
    верхний уровень (папки среди папок, корневые сервисы среди корневых сервисов,
    корневые аккаунты среди корневых аккаунтов). Перенос между родителями и смена
    типа — через контекстное меню."""

    order_changed = Signal(object)   # параметр: элемент-родитель (или None для корня)
    drop_rejected = Signal()

    @staticmethod
    def _item_type(item):
        if item is None:
            return None
        data = item.data(0, Qt.UserRole)
        return data["type"] if data else None

    def dropEvent(self, event):
        sources = self.selectedItems()
        if not sources:
            event.ignore()
            return

        # Все перетаскиваемые элементы — одного родителя и одного типа
        src_parents = {it.parent() for it in sources}
        src_types = {self._item_type(it) for it in sources}
        if len(src_parents) != 1 or len(src_types) != 1:
            event.ignore(); self.drop_rejected.emit(); return
        src_parent = next(iter(src_parents))
        src_type = next(iter(src_types))

        # Куда бросаем: только «между строками» (Above/Below), не «внутрь»
        target = self.itemAt(event.position().toPoint())
        indicator = self.dropIndicatorPosition()
        if indicator not in (QAbstractItemView.AboveItem, QAbstractItemView.BelowItem):
            event.ignore(); self.drop_rejected.emit(); return
        dest_parent = target.parent() if target else None
        dest_type = self._item_type(target)

        # Тот же родитель И тот же тип, что у строки-цели
        if dest_parent is not src_parent or dest_type != src_type:
            event.ignore(); self.drop_rejected.emit(); return

        super().dropEvent(event)
        self.order_changed.emit(src_parent)


class _VaultWriter(QObject):
    """Фоновая запись зашифрованного контейнера на диск.

    Живёт в отдельном потоке. Получает ГОТОВЫЙ снимок БД (db_bytes), сделанный
    в GUI-потоке (там, где живёт соединение SQLite), и выполняет самое тяжёлое —
    шифрование AES-GCM и атомарную запись на диск — не блокируя интерфейс.
    Не обращается к соединению SQLite, поэтому потокобезопасен относительно него."""

    done = Signal(bool, str, bool)   # ok, текст_ошибки, признак_конфликта
    _job = Signal(object, bool)      # внутренний: (db_bytes, force) → в свой поток

    def __init__(self, db):
        super().__init__()
        self._db = db
        # Очередь из одного задания: сигнал доставляется в поток воркера.
        self._job.connect(self._do, Qt.ConnectionType.QueuedConnection)

    def submit(self, db_bytes, force=False):
        self._job.emit(db_bytes, force)

    @Slot(object, bool)
    def _do(self, db_bytes, force):
        ok, err, conflict = True, "", False
        try:
            self._db.seal_and_write(db_bytes, force=force)
        except VaultConflictError:
            ok, conflict, err = False, True, "conflict"
        except Exception as e:                       # noqa: BLE001 — отдаём наверх
            ok, err = False, str(e)
        self.done.emit(ok, err, conflict)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = Config()
        self.setWindowTitle("ХРАНИЛКА v1.0")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.resize(1200, 700)
        self.setMinimumSize(760, 480)

        self.db = Database(str(BASE_DIR / "hranilka.db"))

        # Межпроцессная блокировка: не даём второму экземпляру открыть тот же
        # файл-БД (иначе при сохранении они затёрли бы правки друг друга).
        self._instance_lock = instance_lock.InstanceLock(self.db.db_path)
        try:
            self._instance_lock.acquire()
        except instance_lock.VaultLockedError:
            theme.themed_info(
                self.config, None, "Хранилка уже запущена",
                "Файл базы уже открыт другим экземпляром «Хранилки».\n"
                "Закройте его перед повторным запуском.",
            )
            sys.exit(0)
        # Снятие блокировки при любом завершении процесса (страховка на случай
        # путей выхода помимо closeEvent — например, sys.exit ниже).
        atexit.register(self._instance_lock.release)

        # Отложенная запись на диск (шифр. режим): БД помечает себя «грязной»,
        # а мы сбрасываем её один раз за оборот событийного цикла.
        self._db_flush_scheduled = False
        self.db._on_dirty = self._schedule_db_flush

        # Фоновая запись зашифрованного контейнера (тяжёлые AES-GCM+fsync не
        # должны морозить UI). serialize() остаётся в GUI-потоке, шифрование и
        # запись — в воркере. Без debounce: планируем singleShot(0), но пока
        # идёт запись, новые правки копятся и пишутся одним свежим снимком после.
        self._write_busy = False        # воркер сейчас пишет
        self._write_pending = False     # во время записи появились новые правки
        self._writer_idle_loop = None   # локальный event-loop ожидания (close/lock)
        self._writer = _VaultWriter(self.db)
        self._writer_thread = QThread(self)
        self._writer.moveToThread(self._writer_thread)
        self._writer.done.connect(self._on_vault_written)
        self._writer_thread.start()

        if not self._open_database():
            # Пользователь выбрал «Выход» в окне разблокировки.
            self._shutdown_writer()
            sys.exit(0)
        self._create_tables_or_exit()

        self.current_account_data = None
        self.current_tree_item = None
        self.is_editing = False
        self._is_max = False                 # своё состояние «развёрнуто/окно»
        self.sort_mode = self.config.get("sort_mode", "manual")
        self.sort_desc = self.config.get("sort_desc", False)
        self.search_text = ""

        self._current_account_id = None      # id аккаунта в правой панели
        self._edit_cache = {}                # id -> {"storage":..., "links":[...]} несохранённые правки
        self._dirty_ids = set()              # аккаунты с несохранёнными правками
        self._any_db_changes = False         # True если в этой сессии что-то было записано в БД

        # Таймер авто-очистки буфера обмена
        self._clip_timer = QTimer(self)
        self._clip_timer.setSingleShot(True)
        self._clip_timer.timeout.connect(self._clear_clipboard)

        # Таймер авто-блокировки (idle)
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._check_idle)
        self._last_activity = QDateTime.currentDateTime()
        self._unlocking = False   # True во время повторной разблокировки
        # Глобальный учёт активности: любое действие пользователя в любом окне
        # программы (включая настройки и диалоги) сбрасывает счётчик простоя.
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.setup_ui()
        self.apply_config()
        self._restore_geometry()
        self.populate_tree()

    # Префиксы-«иконки» для элементов дерева
    PREFIX = {"folder": "[+] ", "service": "[o] ", "account": "(i) "}

    def _node_display(self, node):
        """Текст элемента дерева с маркерами избранного, просрочки и несохранённых правок."""
        text = self.PREFIX[node["type"]] + node["name"]
        if node["type"] == "account":
            if node.get("is_favorite"):
                text = "* " + text
            days = node.get("pwd_days_left")
            if days is not None and days <= 0:
                text += "  [!]"   # пора менять пароль
            if node["id"] in self._dirty_ids:
                text = "● НЕ СОХРАНЕНО ▸ " + text   # есть несохранённые правки
        return text

    def _apply_item_style(self, item, node):
        """Текст + визуальное выделение для аккаунтов с несохранёнными правками."""
        item.setText(0, self._node_display(node))
        dirty = node["type"] == "account" and node["id"] in self._dirty_ids
        font = item.font(0)
        font.setBold(dirty)
        item.setFont(0, font)
        item.setForeground(0, QBrush(QColor("#FFC400")) if dirty else QBrush())

    def _node(self, item):
        """Возвращает словарь данных (type, id, name, ...) элемента дерева или None."""
        return item.data(0, Qt.UserRole) if item else None

    def closeEvent(self, event):
        # Аккаунты с несохранёнными правками (+ редактируемый сейчас)
        unsaved = set(self._dirty_ids)
        if self.is_editing and self._current_account_id is not None:
            unsaved.add(self._current_account_id)
        if unsaved and self.config.get("warn_on_exit_unsaved", True):
            n = len(unsaved)
            if not theme.themed_confirm(
                self.config, self, "Несохранённые данные",
                f"Есть несохранённые изменения (аккаунтов: {n}).\nВыйти без сохранения?",
            ):
                event.ignore()
                return

        # Дождаться завершения фоновой записи, чтобы дальнейшее синхронное
        # сохранение/бэкап не конкурировали с воркером за один файл.
        self._wait_writer_idle()

        # Авто-бэкап при закрытии (если включён и были изменения)
        if self.config.get("backup_auto_on_close") and self.config.get("backup_folder"):
            had_changes = bool(unsaved) or self._any_db_changes
            if had_changes:
                # Бэкап копирует файл с диска — сначала сбросить отложенные
                # изменения (актуально для шифрованного режима).
                self.db.flush()
                try:
                    bk.create_backup(
                        self.db.db_path,
                        self.config.get("backup_folder"),
                        self.config.get("backup_keep_count", 5),
                    )
                except Exception as e:
                    logging.warning("Авто-бэкап не удался: %s", e)

        # Сохранение геометрии окна
        if self.config.get("remember_geometry"):
            self.config.set("_window_geometry",
                            bytes(self.saveGeometry().toHex()).decode("ascii"))
            self.config.save()

        # Очистка буфера обмена при выходе (если включено в настройках)
        if self.config.get("clipboard_clear_on_exit", False):
            try:
                QApplication.clipboard().clear()
            except Exception as e:
                logging.warning("Не удалось очистить буфер при выходе: %s", e)

        try:
            self.db.close()
        except VaultConflictError:
            # Файл изменён извне — спрашиваем, перезаписать ли своими данными.
            if theme.themed_confirm(
                self.config, self, "Файл изменён извне",
                "Файл базы был изменён другой программой с момента открытия.\n"
                "Перезаписать его своими данными?",
            ):
                try:
                    self.db.close(force=True)
                except Exception as e:
                    logging.warning("Не удалось сохранить БД при закрытии: %s", e)
                    if not theme.themed_confirm(
                        self.config, self, "Ошибка сохранения",
                        f"Не удалось сохранить базу на диск:\n{e}\n\n"
                        "Выйти, потеряв последние изменения?",
                    ):
                        event.ignore()
                        return
            elif not theme.themed_confirm(
                self.config, self, "Выход",
                "Выйти, не сохранив последние изменения?",
            ):
                event.ignore()
                return
        except Exception as e:
            # Сохранение при закрытии не удалось. Не выходим молча с потерей
            # данных — спрашиваем пользователя.
            logging.warning("Не удалось сохранить БД при закрытии: %s", e)
            if not theme.themed_confirm(
                self.config, self, "Ошибка сохранения",
                f"Не удалось сохранить базу на диск:\n{e}\n\n"
                "Выйти, потеряв последние изменения?",
            ):
                event.ignore()
                return
        # Корректно остановить поток фоновой записи.
        self._shutdown_writer()
        self._instance_lock.release()
        super().closeEvent(event)

    def toggle_maximize(self):
        # Своё состояние, не полагаясь на isMaximized(): у frameless-окна он
        # возвращает недостоверное значение, из-за чего иконка «отставала».
        if self._is_max:
            self.showNormal()
            self._is_max = False
        else:
            self.showMaximized()
            self._is_max = True
        self.title_bar.set_maximized(self._is_max)

    def nativeEvent(self, event_type, message):
        # На Windows frameless-окно иногда "съедает" первый клик (активация окна
        # и клик не передаётся виджету). MA_ACTIVATE (1) = активировать И передать клик.
        if event_type == b"windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == 0x0021:  # WM_MOUSEACTIVATE
                return True, 1
        return super().nativeEvent(event_type, message)

    def _update_dnd_mode(self):
        """DnD-переупорядочивание доступно только в режиме ручной сортировки.
        В остальных режимах перетаскивание полностью выключаем (и сам drag, и
        приём drop), иначе тянущийся элемент превращался бы в выделение."""
        manual = self.sort_mode == "manual"
        self.tree.setDragEnabled(manual)
        self.tree.setDragDropMode(QAbstractItemView.InternalMove if manual
                                  else QAbstractItemView.NoDragDrop)

    def on_sort_changed(self):
        self.sort_mode = self.sort_combo.currentData()
        self.config.set("sort_mode", self.sort_mode)
        self.config.save()
        self._update_dnd_mode()
        self._update_sort_dir_btn()
        self._reload_tree()

    def _update_sort_dir_btn(self):
        self.sort_dir_btn.setText("v" if self.sort_desc else "^")
        # Направление неактуально для ручной сортировки
        self.sort_dir_btn.setEnabled(self.sort_mode != "manual")

    def on_sort_dir_toggled(self):
        self.sort_desc = not self.sort_desc
        self.config.set("sort_desc", self.sort_desc)
        self.config.save()
        self._update_sort_dir_btn()
        self._reload_tree()

    def on_search_changed(self, text):
        self.search_text = text.strip().lower()
        self._apply_filter()

    def _apply_filter(self):
        text = self.search_text

        def visit(item):
            node = self._node(item)
            self_match = (not text) or (text in node["name"].lower())
            child_visible = False
            for i in range(item.childCount()):
                child_visible = visit(item.child(i)) or child_visible
            visible = self_match or child_visible
            item.setHidden(not visible)
            if text and child_visible:
                item.setExpanded(True)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def on_tree_order_changed(self, parent_item):
        """Сохраняет новый порядок после перетаскивания.

        Для корня (parent_item is None) порядок сохраняется по каждой группе
        типов отдельно (папки / корневые сервисы / корневые аккаунты), т.к. они
        хранятся в разных таблицах со своим sort_order."""
        if parent_item is None:
            folder_ids, service_ids, account_ids = [], [], []
            for i in range(self.tree.topLevelItemCount()):
                node = self._node(self.tree.topLevelItem(i))
                {"folder": folder_ids, "service": service_ids,
                 "account": account_ids}[node["type"]].append(node["id"])
            self.db.set_folders_order(folder_ids)
            self.db.set_services_order(service_ids)   # корневые сервисы
            self.db.set_accounts_order(account_ids)   # корневые аккаунты
            self.statusBar().showMessage("Порядок сохранён", 2000)
            return

        parent_node = self._node(parent_item)
        child_ids = [self._node(parent_item.child(i))["id"]
                     for i in range(parent_item.childCount())]
        if parent_node["type"] == "folder":
            self.db.set_services_order(child_ids)
        elif parent_node["type"] == "service":
            self.db.set_accounts_order(child_ids)
        self.statusBar().showMessage("Порядок сохранён", 2000)

    def setup_ui(self):
        # Кастомный заголовок окна вместо системного
        self.title_bar = TitleBar(self)
        self.title_bar.minimize_requested.connect(self.showMinimized)
        self.title_bar.maximize_requested.connect(self.toggle_maximize)
        self.title_bar.close_requested.connect(self.close)
        self.title_bar.settings_requested.connect(self.open_settings)
        self.title_bar.bin_requested.connect(self.open_bin)
        self.setMenuWidget(self.title_bar)

        central_widget = ResizableContainer(self)
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(ResizableContainer.MARGIN, 4,
                                  ResizableContainer.MARGIN, ResizableContainer.MARGIN)

        self.splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(self.splitter)

        # ЛЕВАЯ ПАНЕЛЬ
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        btn_layout = QHBoxLayout()
        self.add_folder_btn = QPushButton(" + ПАПКА ")
        self.add_service_btn = QPushButton(" + СЕРВИС ")
        self.add_account_btn = QPushButton(" + АККАУНТ ")
        self.add_folder_btn.clicked.connect(self.add_folder)
        self.add_service_btn.clicked.connect(self.add_service)
        self.add_account_btn.clicked.connect(self.add_account)
        btn_layout.addWidget(self.add_folder_btn)
        btn_layout.addWidget(self.add_service_btn)
        btn_layout.addWidget(self.add_account_btn)
        left_layout.addLayout(btn_layout)

        # Строка поиска (живой фильтр по названиям)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Поиск по названию...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self.on_search_changed)
        left_layout.addWidget(self.search_box)

        # Строка выбора сортировки + направление
        sort_layout = QHBoxLayout()
        sort_label = QLabel("Сортировка:")
        sort_label.setProperty("heading", "true")
        sort_layout.addWidget(sort_label)
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Вручную", "manual")
        self.sort_combo.addItem("По имени", "name")
        self.sort_combo.addItem("По дате создания", "created")
        self.sort_combo.addItem("Скоро смена пароля", "pwd_due")
        idx = self.sort_combo.findData(self.sort_mode)
        if idx >= 0:
            self.sort_combo.setCurrentIndex(idx)
        self.sort_combo.currentIndexChanged.connect(self.on_sort_changed)
        sort_layout.addWidget(self.sort_combo, 1)

        self.sort_dir_btn = QPushButton()
        self.sort_dir_btn.setFixedWidth(36)
        self.sort_dir_btn.setToolTip("Направление сортировки")
        self.sort_dir_btn.clicked.connect(self.on_sort_dir_toggled)
        self._update_sort_dir_btn()
        sort_layout.addWidget(self.sort_dir_btn)
        left_layout.addLayout(sort_layout)

        self.tree = AccountTree()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(25)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDefaultDropAction(Qt.MoveAction)  # InternalMove = перемещение
        self._update_dnd_mode()
        self.tree.order_changed.connect(self.on_tree_order_changed)
        self.tree.drop_rejected.connect(
            lambda: self.statusBar().showMessage(
                "Перетаскивание меняет порядок только внутри одной группы. "
                "Для переноса используйте ПКМ → «Переместить…»", 4000)
        )
        self.tree.currentItemChanged.connect(self.on_item_selected)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_tree_context_menu)
        left_layout.addWidget(self.tree)
        self.splitter.addWidget(left_panel)
        
        # ПРАВАЯ ПАНЕЛЬ
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 0, 10, 5)
        
        self.placeholder_label = QLabel("\n\n[ ВЫБЕРИТЕ АККАУНТ ИЗ ДЕРЕВА ]\n\n")
        self.placeholder_label.setProperty("heading", "true")
        self.placeholder_label.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(self.placeholder_label)
        
        self.tabs = AccountTabs(config=self.config)
        self.tabs.hide()
        right_layout.addWidget(self.tabs)

        # Связанные аккаунты: навигация и добавление
        self.tabs.f_linked.navigate_requested.connect(self.on_link_navigate)
        self.tabs.f_linked.add_requested.connect(self.on_add_link_requested)

        # Постоянный индикатор в статус-баре (путь + режим)
        self.status_info = QLabel("")
        self.statusBar().addPermanentWidget(self.status_info)

        # Подключаем ВСЕ сигналы копирования к статус-бару
        copy_fields = [
            self.tabs.f_name, self.tabs.f_url, self.tabs.f_login, self.tabs.f_password,
            self.tabs.f_first, self.tabs.f_last, self.tabs.f_middle, 
            self.tabs.f_address, self.tabs.f_device_id, self.tabs.f_ip, 
            self.tabs.f_browser, self.tabs.f_os,
            self.tabs.f_creation_date, self.tabs.f_password_date, self.tabs.f_birth,
            self.tabs.f_notes, self.tabs.f_recovery,
            self.tabs.f_codes_widget
        ]
        for field in copy_fields:
            field.copy_signal.connect(self._on_field_copied)
        
        # КНОПКИ РЕДАКТИРОВАНИЯ (Они на месте и работают)
        self.action_layout = QHBoxLayout()
        
        self.edit_btn = QPushButton(" РЕДАКТИРОВАТЬ ")
        self.edit_btn.clicked.connect(self.toggle_edit_mode)
        
        self.save_btn = QPushButton(" СОХРАНИТЬ ")
        self.save_btn.clicked.connect(self.save_account)
        
        self.cancel_btn = QPushButton(" ОТМЕНА ")
        self.cancel_btn.clicked.connect(self.cancel_edit)
        
        self.action_layout.addWidget(self.edit_btn)
        self.action_layout.addWidget(self.save_btn)
        self.action_layout.addWidget(self.cancel_btn)
        self.action_layout.addStretch()
        right_layout.addLayout(self.action_layout)
        
        # Изначально скрываем кнопки, пока аккаунт не выбран
        self.edit_btn.hide()
        self.save_btn.hide()
        self.cancel_btn.hide()
        
        self.splitter.addWidget(right_panel)
        self.splitter.setSizes([350, 850])
        
        # Кнопки генерации
        self.tabs.gen_pass_btn.clicked.connect(self.generate_password)
        self.tabs.gen_pd_btn.clicked.connect(self.generate_personal_data)

        # Горячие клавиши (после создания дерева и поля поиска — они нужны как
        # цели для контекстных шорткатов Del/Esc)
        self._setup_shortcuts()

    def apply_appearance(self):
        """Только визуальная часть (шрифт, стили). Лёгкая — подходит для живого
        предпросмотра настроек, не трогает геометрию/таймеры/БД."""
        font_name = self.config.get("font", "Cascadia Code")
        font_size = self.config.get("font_size", 14)
        text_color = self.config.get("text_color", "#FFFFFF")
        tree_bg = self.config.get("tree_bg_color", "#0000AA")
        main_bg = self.config.get("main_bg_color", "#0000AA")

        self.setFont(QFont(font_name, font_size))
        # Общий стиль окна (вкл. кнопки-вкладки QPushButton[tabButton], спинбоксы, комбобоксы)
        self.setStyleSheet(theme.main_stylesheet(self.config))
        self.tree.setStyleSheet(f"""
            QTreeWidget {{ border: 2px inset #808080; background-color: {tree_bg}; color: {text_color}; font-family: '{font_name}'; font-size: {font_size}px; }}
            QTreeWidget::item {{ padding: 4px; border: 1px solid transparent; }}
            QTreeWidget::item:hover {{ background-color: {main_bg}; }}
            QTreeWidget::item:selected {{ background-color: {text_color}; color: {tree_bg}; }}
        """ + self._branch_arrow_css(text_color, tree_bg))

    def _branch_arrow_css(self, text_color, tree_bg):
        """Стрелки сворачивания/разворачивания, перекрашенные под тему.

        Стандартные стрелки рисуются стилем ОС фиксированным цветом и теряются
        на выделении (фон строки = text_color). Генерируем свои треугольники:
        в обычном состоянии — цветом текста, на выделении — цветом фона дерева
        (как инвертируется текст), чтобы стрелка всегда оставалась видимой."""
        from PySide6.QtGui import QPixmap, QPainter, QPolygon, QColor
        from PySide6.QtCore import QPoint
        import tempfile
        import os as _os
        if not hasattr(self, "_branch_icon_dir"):
            self._branch_icon_dir = tempfile.mkdtemp(prefix="hranilka_branch_")

        def make(name, direction, color):
            pm = QPixmap(16, 16)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(QColor(color))
            p.setPen(Qt.PenStyle.NoPen)
            if direction == "closed":      # ▶
                p.drawPolygon(QPolygon([QPoint(5, 3), QPoint(11, 8), QPoint(5, 13)]))
            else:                          # ▼
                p.drawPolygon(QPolygon([QPoint(3, 5), QPoint(13, 5), QPoint(8, 11)]))
            p.end()
            path = _os.path.join(self._branch_icon_dir, name + ".png")
            pm.save(path, "PNG")
            return path.replace("\\", "/")

        cn = make("closed_n", "closed", text_color)
        op = make("open_n", "open", text_color)
        cs = make("closed_s", "closed", tree_bg)
        ops = make("open_s", "open", tree_bg)
        return f"""
            QTreeWidget::branch:has-children:closed {{ image: url("{cn}"); }}
            QTreeWidget::branch:has-children:open {{ image: url("{op}"); }}
            QTreeWidget::branch:has-children:closed:selected {{ image: url("{cs}"); }}
            QTreeWidget::branch:has-children:open:selected {{ image: url("{ops}"); }}
        """

    def apply_config(self):
        """Полное применение настроек: внешний вид + скриншот-защита, idle-таймер,
        кнопка корзины. Вызывается при старте и при реальном «Применить»
        (геометрия восстанавливается только при старте — см. _restore_geometry)."""
        self.apply_appearance()

        # Скриншот-защита
        self._apply_screenshot_protect(self.config.get("screenshot_protect", False))

        # Idle-таймер (проверка каждые 30 с)
        if self.config.get("idle_lock_mins", 0) > 0:
            self._idle_timer.start(30_000)
        else:
            self._idle_timer.stop()

        # Кнопка корзины (видимость/счётчик зависят от настройки и содержимого)
        self._update_bin_button()

    def _restore_geometry(self):
        """Восстановить размер/положение окна (только при старте)."""
        if self.config.get("remember_geometry"):
            geo_hex = self.config.get("_window_geometry", "")
            if geo_hex:
                self.restoreGeometry(QByteArray.fromHex(bytes(geo_hex, "ascii")))

    def populate_tree(self):
        self.tree.clear()
        for node in self.db.get_tree_structure(self.sort_mode, self.sort_desc):
            self._add_tree_node(self.tree, node)
        self._apply_filter()

    def _add_tree_node(self, parent, node):
        data = {k: v for k, v in node.items() if k != "children"}
        item = QTreeWidgetItem(parent, [""])
        item.setData(0, Qt.UserRole, data)
        item.setToolTip(0, node["name"])
        self._apply_item_style(item, node)
        if node["type"] in ("folder", "service"):
            item.setExpanded(True)
        for child in node.get("children", []):
            self._add_tree_node(item, child)
        return item

    def _iter_items(self):
        """Обход всех элементов дерева."""
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(item.child(i) for i in range(item.childCount()))

    def _reload_tree(self):
        """Перестраивает дерево из БД, сохраняя раскрытие и выбранный элемент.

        Сигналы дерева блокируются, чтобы перестроение не вызывало повторную
        загрузку правой панели и логику стэша несохранённых правок."""
        expanded = {(self._node(it)["type"], self._node(it)["id"])
                    for it in self._iter_items() if it.isExpanded()}
        sel = self._node(self.tree.currentItem())
        sel_key = (sel["type"], sel["id"]) if sel else None

        self.tree.blockSignals(True)
        self.populate_tree()

        for it in self._iter_items():
            node = self._node(it)
            if node["type"] in ("folder", "service"):
                it.setExpanded((node["type"], node["id"]) in expanded)
            if sel_key and (node["type"], node["id"]) == sel_key:
                self.tree.setCurrentItem(it)
                self.current_tree_item = it
        self.tree.blockSignals(False)

        # Восстановление раскрытия могло «свернуть» совпадения — применяем фильтр снова
        if self.search_text:
            self._apply_filter()

    def _select_node(self, node_type, node_id):
        for it in self._iter_items():
            n = self._node(it)
            if n["type"] == node_type and n["id"] == node_id:
                self.tree.setCurrentItem(it)
                return

    # ----- Контекстное меню -----

    def show_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        selected = self.tree.selectedItems()
        if item not in selected:
            self.tree.setCurrentItem(item)
            selected = [item]
        nodes = [self._node(i) for i in selected]
        types = {n["type"] for n in nodes}
        menu = QMenu(self)

        if len(selected) == 1:
            node = nodes[0]
            t = node["type"]
            if t == "account":
                self._add_move_to_service_menu(menu, selected)
                fav = node.get("is_favorite")
                menu.addAction("Убрать из избранного" if fav else "В избранное",
                               lambda: self._set_favorite(selected, not fav))
                menu.addSeparator()
                menu.addAction("Экспорт…", lambda: self.open_export(node))
                menu.addAction("Удалить", lambda: self.delete_items(selected))
            elif t == "service":
                menu.addAction("Переименовать", lambda: self.rename_item(item))
                self._add_move_to_folder_menu(menu, selected)
                self._add_delete_menu(menu, selected, with_keep=True)
                menu.addSeparator()
                menu.addAction("Экспорт…", lambda: self.open_export(node))
                menu.addAction("Раскрыть всё", lambda: self.set_expanded(item, True))
                menu.addAction("Свернуть всё", lambda: self.set_expanded(item, False))
            elif t == "folder":
                menu.addAction("Переименовать", lambda: self.rename_item(item))
                self._add_delete_menu(menu, selected, with_keep=True)
                menu.addSeparator()
                menu.addAction("Экспорт…", lambda: self.open_export(node))
                menu.addAction("Раскрыть всё", lambda: self.set_expanded(item, True))
                menu.addAction("Свернуть всё", lambda: self.set_expanded(item, False))
        else:
            # Множественный выбор
            if types == {"service"}:
                self._add_move_to_folder_menu(menu, selected)
                self._add_delete_menu(menu, selected, with_keep=True)
            elif types == {"account"}:
                self._add_move_to_service_menu(menu, selected)
                menu.addAction("В избранное", lambda: self._set_favorite(selected, True))
                menu.addAction("Убрать из избранного", lambda: self._set_favorite(selected, False))
                menu.addSeparator()
                menu.addAction("Удалить", lambda: self.delete_items(selected))
            else:
                menu.addAction("Удалить", lambda: self.delete_items(selected))

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _add_delete_menu(self, menu, selected, with_keep):
        menu.addSeparator()
        if with_keep:
            sub = menu.addMenu("Удалить")
            sub.addAction("С сохранением содержимого",
                          lambda: self.delete_items(selected, keep=True))
            sub.addAction("Без сохранения (полностью)",
                          lambda: self.delete_items(selected, keep=False))
        else:
            menu.addAction("Удалить", lambda: self.delete_items(selected))

    def _add_move_to_folder_menu(self, menu, selected):
        sub = menu.addMenu("Переместить в папку")
        sub.addAction("Новая папка…", lambda: self._move_services_new_folder(selected))
        folders = self.db.get_folders()
        if folders:
            sub.addSeparator()
            for f in folders:
                sub.addAction(f["name"],
                              lambda checked=False, fid=f["id"]: self._move_services(selected, fid))
        sub.addSeparator()
        sub.addAction("Вынести из папки", lambda: self._move_services(selected, None))

    def _add_move_to_service_menu(self, menu, selected):
        sub = menu.addMenu("Переместить в сервис")
        sub.addAction("Новый сервис…", lambda: self._move_accounts_new_service(selected))
        # Текущие сервисы выбранных аккаунтов (по их родителю в дереве): сервис,
        # в котором уже находятся ВСЕ выбранные, не предлагаем (перенос — no-op).
        current = set()
        for it in selected:
            parent = it.parent()
            pnode = self._node(parent) if parent else None
            current.add(pnode["id"] if pnode and pnode["type"] == "service" else None)
        services = [s for s in self.db.get_services() if current != {s["id"]}]
        if services:
            sub.addSeparator()
            for s in services:
                sub.addAction(s["name"],
                              lambda checked=False, sid=s["id"]: self._move_accounts(selected, sid))
        sub.addSeparator()
        sub.addAction("Сделать свободным (без сервиса)",
                      lambda: self._move_accounts(selected, None))

    # ----- Операции меню -----

    def rename_item(self, item):
        node = self._node(item)
        new_name, ok = theme.themed_input(
            self.config, self, "Переименование", "Новое название:", node["name"]
        )
        if ok and new_name:
            if node["type"] == "folder":
                self.db.rename_folder(node["id"], new_name)
            elif node["type"] == "service":
                self.db.rename_service(node["id"], new_name)
            self._reload_tree()

    def set_expanded(self, item, state):
        item.setExpanded(state)
        for i in range(item.childCount()):
            self.set_expanded(item.child(i), state)

    def delete_items(self, selected, keep=False):
        nodes = [self._node(i) for i in selected]
        if len(nodes) == 1:
            msg = f"Удалить '{nodes[0]['name']}'?"
        else:
            msg = f"Удалить выбранные элементы ({len(nodes)} шт.)?"
        if keep:
            msg += "\nСодержимое будет сохранено (перемещено на уровень выше)."
        if not theme.themed_confirm(self.config, self, "Подтверждение", msg):
            return

        to_bin = self.config.get("recycle_bin_enabled", False)
        for node in nodes:
            if node["type"] == "folder":
                # При полном удалении (не keep) аккаунты внутри тоже исчезают —
                # чистим их кэш несохранённых правок, иначе остаётся «мусор» и
                # ложное предупреждение о несохранённых данных.
                if not keep:
                    self._forget_account_cache(
                        self.db.get_descendant_account_ids("folder", node["id"]))
                (self.db.delete_folder_keep_content if keep else self.db.delete_folder)(node["id"])
            elif node["type"] == "service":
                if not keep:
                    self._forget_account_cache(
                        self.db.get_descendant_account_ids("service", node["id"]))
                (self.db.delete_service_keep_content if keep else self.db.delete_service)(node["id"])
            else:
                # Аккаунты при включённой корзине удаляются мягко (в корзину).
                if to_bin:
                    self.db.move_account_to_bin(node["id"])
                else:
                    self.db.delete_account(node["id"])
                self._forget_account_cache([node["id"]])
        self._any_db_changes = True

        self.current_tree_item = None
        self._current_account_id = None
        self.is_editing = False
        self._reload_tree()
        self._show_placeholder()
        self._update_bin_button()
        self.statusBar().showMessage("Удалено", 2000)

    def _forget_account_cache(self, account_ids):
        """Удаляет несохранённые правки/пометки указанных аккаунтов из памяти."""
        for aid in account_ids:
            self._edit_cache.pop(aid, None)
            self._dirty_ids.discard(aid)

    def _set_favorite(self, selected, value):
        for node in (self._node(i) for i in selected):
            if node["type"] == "account":
                self.db.set_favorite(node["id"], value)
        self._reload_tree()

    def _move_services(self, selected, folder_id):
        for node in (self._node(i) for i in selected):
            if node["type"] == "service":
                self.db.move_service(node["id"], folder_id)
        self._reload_tree()
        self.statusBar().showMessage("Перемещено", 2000)

    def _move_services_new_folder(self, selected):
        name, ok = theme.themed_input(self.config, self, "Новая папка", "Название:")
        if ok and name:
            folder_id = self.db.add_folder(name)
            self._move_services(selected, folder_id)

    def _move_accounts(self, selected, service_id):
        for node in (self._node(i) for i in selected):
            if node["type"] == "account":
                self.db.move_account(node["id"], service_id)
        self._reload_tree()
        self.statusBar().showMessage("Перемещено", 2000)

    def _move_accounts_new_service(self, selected):
        name, ok = theme.themed_input(self.config, self, "Новый сервис", "Название:")
        if ok and name:
            service_id = self.db.add_service(name, None)
            self._move_accounts(selected, service_id)

    # ----- Добавление элементов -----

    def add_folder(self):
        name, ok = theme.themed_input(self.config, self, "Новая папка", "Название:")
        if ok and name:
            folder_id = self.db.add_folder(name)
            self._any_db_changes = True
            self._reload_tree()
            self._select_node("folder", folder_id)

    def add_service(self):
        # Сервис можно добавить в выбранную папку либо как самостоятельный
        # (вне папки) — оба варианта допускаются ТЗ.
        current = self.tree.currentItem()
        node = self._node(current)
        folder_id = node["id"] if node and node["type"] == "folder" else None

        name, ok = theme.themed_input(self.config, self, "Новый сервис", "Название:")
        if ok and name:
            service_id = self.db.add_service(name, folder_id)
            self._any_db_changes = True
            self._reload_tree()
            self._select_node("service", service_id)

    def add_account(self):
        current = self.tree.currentItem()
        node = self._node(current)
        if node and node["type"] == "service":
            service_id = node["id"]
        elif node and node["type"] == "account":
            # Аккаунт под сервисом → тот же сервис; свободный аккаунт → корень.
            parent = current.parent()
            pnode = self._node(parent) if parent else None
            service_id = pnode["id"] if pnode and pnode["type"] == "service" else None
        else:
            # Папка или ничего не выбрано → свободный аккаунт (в корне).
            service_id = None

        name, ok = theme.themed_input(self.config, self, "Новый аккаунт", "Название:")
        if ok and name:
            account_id = self.db.add_account(service_id, name)
            data = AccountData()
            data.name = name
            self.db.save_account(account_id, data.to_storage())  # поля по умолчанию
            self._any_db_changes = True
            self._reload_tree()
            self._select_node("account", account_id)

    def _show_placeholder(self):
        self.placeholder_label.show()
        self.tabs.hide()
        self.edit_btn.hide()
        self.save_btn.hide()
        self.cancel_btn.hide()

    def on_item_selected(self, current, previous):
        node = self._node(current)
        new_id = node["id"] if (node and node["type"] == "account") else None

        # Уходим с аккаунта, который сейчас редактируется → стэшим правки (не теряем их)
        if (self.is_editing and self._current_account_id is not None
                and self._current_account_id != new_id):
            self._stash_current_edits(self._current_account_id)
            self._refresh_dirty_markers()

        if new_id is None:
            self._current_account_id = None
            self.is_editing = False
            self._show_placeholder()
            self._update_status_info()
            return

        self.placeholder_label.hide()
        self.tabs.show()
        self.current_tree_item = current
        self._current_account_id = new_id

        if new_id in self._edit_cache:
            # Возврат к аккаунту с несохранёнными правками — восстанавливаем
            cached = self._edit_cache[new_id]
            self.current_account_data = AccountData.from_storage(cached["storage"])
            self.load_data_to_ui()
            links = [{"id": i, "name": self.db.get_account_path(i)} for i in cached["links"]]
            self.tabs.f_linked.set_data(links)
            self.is_editing = True
            self.tabs.set_all_editable(True)
            self.edit_btn.hide(); self.save_btn.show(); self.cancel_btn.show()
        else:
            self.current_account_data = AccountData.from_storage(self.db.load_account(new_id))
            self.is_editing = False
            self.load_data_to_ui()
            self.tabs.set_all_editable(False)
            self.edit_btn.show(); self.save_btn.hide(); self.cancel_btn.hide()

        self._update_status_info()
        self._warn_password_due(new_id)

    def _collect_account_data(self):
        """Собирает AccountData из полей UI (без записи в БД)."""
        d = self.current_account_data
        # История паролей: если пароль был задан и изменился, дописываем строку
        # в конец заметок (см. _append_password_history).
        old_password = d.password
        new_password = self.tabs.f_password.get_text()
        notes = self._append_password_history(
            self.tabs.f_notes.get_text(), old_password, new_password)

        d.name = self.tabs.f_name.get_text()
        d.url = self.tabs.f_url.get_text()
        d.creation_date = self.tabs.f_creation_date.get_date()
        _pwd = self.tabs.f_password_date.get_date()
        d.password_changed_date = _pwd.date() if _pwd else None
        d.notes = notes
        d.login = self.tabs.f_login.get_text()
        d.password = new_password
        d.first_name = self.tabs.f_first.get_text()
        d.last_name = self.tabs.f_last.get_text()
        d.middle_name = self.tabs.f_middle.get_text()
        _bd = self.tabs.f_birth.get_date()
        d.birth_date = _bd.date() if _bd else None
        d.address = self.tabs.f_address.get_text()
        d.recovery_phrase = self.tabs.f_recovery.get_text()
        d.device_id = self.tabs.f_device_id.get_text()
        d.ip = self.tabs.f_ip.get_text()
        d.browser = self.tabs.f_browser.get_text()
        d.os = self.tabs.f_os.get_text()
        d.password_change_interval_days = self.tabs.f_pwd_interval.get_value()
        d.secret_questions = self.tabs.f_questions_widget.get_data()
        d.one_time_codes = self.tabs.f_codes_widget.get_data()
        d.gallery = self.tabs.f_gallery_widget.get_data()
        return d

    def _append_password_history(self, notes, old_password, new_password):
        """Если пароль был непустым и изменился — дописывает в конец заметок
        строку «Пароль {старый} изменён: {дата+время}» и обновляет поле заметок
        в интерфейсе. Возвращает итоговый текст заметок."""
        if not old_password or new_password == old_password:
            return notes
        stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        line = f"Пароль {old_password} изменен: {stamp}"
        notes = (notes + "\n" + line) if notes else line
        self.tabs.f_notes.set_text(notes)   # отразить в UI сразу
        return notes

    def _stash_current_edits(self, account_id):
        """Сохраняет несохранённые правки аккаунта в память (не в БД)."""
        d = self._collect_account_data()
        self._edit_cache[account_id] = {
            "storage": d.to_storage(),
            "links": self.tabs.f_linked.get_data(),
        }
        self._dirty_ids.add(account_id)

    def _refresh_dirty_markers(self):
        """Обновляет подписи и выделение элементов (метка несохранённых правок)."""
        for it in self._iter_items():
            self._apply_item_style(it, self._node(it))

    def load_data_to_ui(self):
        d = self.current_account_data
        self.tabs.f_name.set_text(d.name)
        self.tabs.f_url.set_text(d.url)
        self.tabs.f_creation_date.set_date(d.creation_date)
        self.tabs.f_password_date.set_date(d.password_changed_date)  # Теперь просто передаем QDate
        self.tabs.f_pwd_interval.set_value(d.password_change_interval_days)
        self.tabs.f_notes.set_text(d.notes)
        self.tabs.f_login.set_text(d.login)
        self.tabs.f_password.set_text(d.password)
        self.tabs.f_first.set_text(d.first_name)
        self.tabs.f_last.set_text(d.last_name)
        self.tabs.f_middle.set_text(d.middle_name)
        self.tabs.f_birth.set_date(d.birth_date)  # Теперь просто передаем QDate
        self.tabs.f_address.set_text(d.address)
        self.tabs.f_recovery.set_text(d.recovery_phrase)
        self.tabs.f_device_id.set_text(d.device_id)
        self.tabs.f_ip.set_text(d.ip)
        self.tabs.f_browser.set_text(d.browser)
        self.tabs.f_os.set_text(d.os)

        self.tabs.f_questions_widget.set_data(d.secret_questions)
        self.tabs.f_codes_widget.set_data(d.one_time_codes)
        self.tabs.f_gallery_widget.set_data(d.gallery)

        # Связанные аккаунты грузим напрямую из БД (это отношение, не поле аккаунта)
        node = self._node(self.current_tree_item)
        self.tabs.f_linked.set_data(self.db.get_links(node["id"]) if node else [])

    def toggle_edit_mode(self):
        self.is_editing = True
        self.tabs.set_all_editable(True)
        # Переключаем кнопки
        self.edit_btn.hide()
        self.save_btn.show()
        self.cancel_btn.show()
        self._update_status_info()

    def cancel_edit(self):
        # Отмена отбрасывает несохранённые правки этого аккаунта
        aid = self._current_account_id
        self._edit_cache.pop(aid, None)
        self._dirty_ids.discard(aid)

        self.is_editing = False
        self.current_account_data = AccountData.from_storage(self.db.load_account(aid))
        self.load_data_to_ui()
        self.tabs.set_all_editable(False)
        self.edit_btn.show()
        self.save_btn.hide()
        self.cancel_btn.hide()
        self._update_status_info()
        self._reload_tree()  # убрать метку несохранённых правок

    def save_account(self):
        d = self._collect_account_data()
        aid = self._current_account_id
        self.db.save_account(aid, d.to_storage())
        self.db.set_links(aid, self.tabs.f_linked.get_data())

        self._edit_cache.pop(aid, None)
        self._dirty_ids.discard(aid)
        self._any_db_changes = True

        self.is_editing = False
        self.tabs.set_all_editable(False)
        self.edit_btn.show()
        self.save_btn.hide()
        self.cancel_btn.hide()
        # Перестраиваем дерево, чтобы обновить имя/маркеры (избранное, срок пароля)
        self._reload_tree()
        self.statusBar().showMessage("СОХРАНЕНО!", 2000)

    def generate_password(self):
        self.tabs.f_password.set_text(util.generate_password())
        self.statusBar().showMessage("ПАРОЛЬ СГЕНЕРИРОВАН", 2000)

    def generate_personal_data(self):
        choice, ok = theme.themed_choice(
            self.config, self, "Генерация ПД", "Выберите национальность:",
            ["Русский", "Американец"],
        )
        if not ok:
            return
        lang = "ru" if choice == "Русский" else "en"
        person = pd_generator.generate_person(lang)

        self.tabs.f_first.set_text(person["first"])
        self.tabs.f_last.set_text(person["last"])
        self.tabs.f_middle.set_text(person["middle"])
        bd = person["birth_date"]
        self.tabs.f_birth.set_date(QDate(bd.year, bd.month, bd.day))
        self.statusBar().showMessage("ПД СГЕНЕРИРОВАНЫ", 2000)

    # ----- Горячие клавиши -----

    # Действия, чьи шорткаты должны срабатывать только когда фокус в дереве —
    # иначе Del/Backspace перехватывался бы у текстовых полей.
    _SHORTCUT_TREE_CTX = {"delete_selected"}

    def _setup_shortcuts(self):
        """Создаёт QShortcut'ы по реестру shortcuts.effective(config).
        Объекты складываются в self._shortcuts для пере-привязки."""
        self._shortcuts = {}
        mapping = shortcuts.effective(self.config)
        for action_id, seq in mapping.items():
            if not seq:                      # снятое сочетание — не вешаем
                continue
            if action_id in self._SHORTCUT_TREE_CTX:
                target, ctx = self.tree, Qt.ShortcutContext.WidgetWithChildrenShortcut
            else:
                target, ctx = self, Qt.ShortcutContext.WindowShortcut
            sc = QShortcut(QKeySequence(seq), target)
            sc.setContext(ctx)
            sc.activated.connect(lambda aid=action_id: self._run_shortcut(aid))
            self._shortcuts[action_id] = sc

    def _rebind_shortcuts(self):
        """Пересоздаёт хоткеи после применения настроек (учитывает добавление и
        снятие сочетаний)."""
        for sc in self._shortcuts.values():
            sc.setEnabled(False)
            sc.deleteLater()
        self._setup_shortcuts()

    def _run_shortcut(self, action_id):
        handlers = {
            "add_account":     self.add_account,
            "add_folder":      self.add_folder,
            "add_service":     self.add_service,
            "edit_account":    self._sc_edit_account,
            "save_account":    self._sc_save_account,
            "cancel_edit":     self._sc_cancel_edit,
            "gen_password":    self._sc_gen_password,
            "gen_personal":    self._sc_gen_personal,
            "delete_selected": self._sc_delete_selected,
            "focus_search":    self._sc_focus_search,
            "toggle_expand":   self._sc_toggle_expand,
            "open_settings":   self.open_settings,
            "open_bin":        self.open_bin,
            "export_all":      self.export_all,
        }
        fn = handlers.get(action_id)
        if fn:
            fn()

    # Обёртки контекстно-зависимых действий: безопасно «ничего не делают»,
    # если действие сейчас неприменимо.
    def _sc_edit_account(self):
        if self._current_account_id and not self.is_editing:
            self.toggle_edit_mode()

    def _sc_save_account(self):
        if self.is_editing:
            self.save_account()

    def _sc_cancel_edit(self):
        if self.is_editing:
            self.cancel_edit()

    def _sc_gen_password(self):
        if self.is_editing:
            self.generate_password()

    def _sc_gen_personal(self):
        if self.is_editing:
            self.generate_personal_data()

    def _sc_delete_selected(self):
        selected = self.tree.selectedItems()
        if selected:
            self.delete_items(selected)

    def _sc_focus_search(self):
        self.search_box.setFocus()
        self.search_box.selectAll()

    def _sc_toggle_expand(self):
        # Раскрыт хотя бы один верхнеуровневый узел → свернуть всё, иначе раскрыть.
        top = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        if not top:
            return
        expand = not any(it.isExpanded() for it in top)
        for it in top:
            self.set_expanded(it, expand)

    def export_all(self):
        """Экспорт всей базы — то же, что кнопка «Экспортировать всё» в настройках."""
        tree = self.db.export_subtree()
        if not tree:
            self.statusBar().showMessage("Нечего экспортировать", 3000)
            return
        ExportDialog(self.config, tree, "Вся база", self).exec()

    # ----- Связанные аккаунты -----

    def on_link_navigate(self, account_id):
        self._select_node("account", account_id)

    def on_add_link_requested(self):
        node = self._node(self.current_tree_item)
        if not node:
            return
        candidates = [a for a in self.db.get_all_accounts() if a["id"] != node["id"]]
        chosen, ok = theme.themed_multiselect(
            self.config, self, "Связать аккаунты", candidates, self.tabs.f_linked.get_data()
        )
        if ok:
            chosen_set = set(chosen)
            links = [{"id": a["id"], "name": a["name"]} for a in candidates if a["id"] in chosen_set]
            self.tabs.f_linked.set_data(links)
            self.tabs.f_linked.set_editable(self.is_editing)

    # ----- Статус-бар -----

    def _update_status_info(self):
        node = self._node(self.current_tree_item)
        if not node or node["type"] != "account":
            self.status_info.setText("")
            return
        path = self.db.get_account_path(node["id"])
        prefix = "[РЕД] " if self.is_editing else "[ПРОСМОТР] "
        self.status_info.setText(prefix + path)

    def _warn_password_due(self, account_id):
        node = self._node(self.current_tree_item)
        days = node.get("pwd_days_left") if node else None
        if days is None:
            return
        if days <= 0:
            self.statusBar().showMessage("ПОРА СМЕНИТЬ ПАРОЛЬ ДЛЯ ЭТОГО АККАУНТА!", 5000)
        elif days <= 7:
            self.statusBar().showMessage(f"Смена пароля через {days} дн.", 4000)

    def open_settings(self):
        dialog = SettingsDialog(self.config, self)
        dialog.set_db_path(self.db.db_path)
        dialog.set_db(self.db)
        dialog.appearance_changed.connect(self.apply_appearance)  # лёгкий предпросмотр
        dialog.settings_applied.connect(self.apply_config)        # полное применение
        dialog.settings_applied.connect(self._rebind_shortcuts)   # пере-привязка хоткеев
        # Восстановление из бэкапа перечитывает БД сразу, окно настроек остаётся открытым.
        dialog.restore_requested.connect(self._reload_database)
        dialog.exec()
        if dialog._delete_all_confirmed:
            self._wipe_all_data()

    def open_export(self, node):
        """Экспорт поддерева (папка/сервис/аккаунт) в выбранный формат.
        Данные берутся из текущей БД (в шифр. режиме — из памяти)."""
        if node["type"] == "account":
            title = self.db.get_account_path(node["id"])
        else:
            title = node["name"]
        tree = self.db.export_subtree(node["type"], node["id"])
        if not tree:
            self.statusBar().showMessage("Нечего экспортировать", 3000)
            return
        ExportDialog(self.config, tree, title, self).exec()

    def _update_bin_button(self):
        """Синхронизирует кнопку корзины в заголовке с числом аккаунтов в
        корзине (пустая корзина — кнопка скрыта)."""
        try:
            count = self.db.get_deleted_count()
        except Exception:
            count = 0
        self.title_bar.update_bin(count)

    def open_bin(self):
        dlg = RecycleBinDialog(self.config, self.db, self)
        dlg.exec()
        if dlg.changed:
            self._reload_tree()
            self._update_bin_button()

    def _archive_db_file(self):
        """Отложить (переименовать) текущий файл БД, не удаляя его."""
        import os
        from datetime import datetime
        if not os.path.exists(self.db.db_path):
            return
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        archived = f"{self.db.db_path}.locked-{ts}"
        try:
            os.replace(self.db.db_path, archived)
            logging.info("Зашифрованный файл отложен: %s", archived)
        except OSError as e:
            logging.warning("Не удалось отложить файл БД: %s", e)

    def _create_tables_or_exit(self):
        """create_tables() с понятным отказом, если база создана более новой
        версией программы (схема новее поддерживаемой). «Миграция вниз»
        повредила бы данные, поэтому корректнее завершить работу."""
        try:
            self.db.create_tables()
        except FutureSchemaError as e:
            logging.error("Несовместимая версия схемы БД: %s", e)
            theme.themed_info(
                self.config, self, "Несовместимая версия базы", str(e),
            )
            self._shutdown_writer()
            self._instance_lock.release()
            sys.exit(1)

    def _open_database(self, parent=None):
        """Открыть БД: зашифрованную — через ввод мастер-пароля/recovery-кода,
        обычную — напрямую. При утере секрета пользователь может восстановить
        бэкап или начать с чистой базы. Возвращает True при успехе, False если
        пользователь выбрал «Выход». Источник истины о шифровании — сигнатура
        файла."""
        import crypto_store as cs
        import shutil
        from dialogs import UnlockDialog
        while True:
            if not cs.is_encrypted_file(self.db.db_path):
                self.db.connect()
                self._create_tables_or_exit()
                self.config.set("encryption_enabled", False)
                self.config.save()
                return True

            with open(self.db.db_path, "rb") as f:
                container = f.read()
            dlg = UnlockDialog(self.config, container, parent)
            dlg.exec()

            if dlg.result_data is not None:
                self.db.open_encrypted(*dlg.result_data)
                self.config.set("encryption_enabled", True)
                self.config.save()
                return True

            if dlg.recovery_action == "reset":
                self._archive_db_file()
                # Файла нет → connect() создаст новую пустую базу.
                continue

            if dlg.recovery_action == "restore" and dlg.restore_path:
                self._archive_db_file()
                try:
                    shutil.copy2(dlg.restore_path, self.db.db_path)
                except OSError as e:
                    theme.themed_info(self.config, parent, "Ошибка",
                                      f"Не удалось восстановить бэкап:\n{e}")
                # Повторяем цикл: восстановленный файл может быть как обычным,
                # так и зашифрованным (другим паролем) — тогда снова спросим.
                continue

            return False  # «Выход»

    def _reload_database(self):
        """Перечитать БД после восстановления бэкапа (учитывает шифрование).

        Закрываем БЕЗ сохранения: иначе текущая in-memory база перезаписала бы
        только что восстановленный файл. Шифрование определяется по сигнатуре
        восстановленного файла — пароль спрашивается, только если он зашифрован."""
        self.db.close(persist=False)
        if not self._open_database(self):
            self.close()
            return
        self._current_account_id = None
        self._edit_cache.clear()
        self._dirty_ids.clear()
        self.current_account_data = None
        self.is_editing = False
        self._any_db_changes = False
        self._show_placeholder()
        self._reload_tree()
        self._update_bin_button()
        self.statusBar().showMessage("База данных восстановлена из бэкапа.", 3000)

    # ─── Отложенная запись БД на диск (шифр. режим) ──────────────────────────

    def _schedule_db_flush(self):
        """Запланировать сброс БД на диск к концу оборота событийного цикла.
        Серия операций (например, цикл по мультивыбору) схлопывается в одну
        запись."""
        if not self._db_flush_scheduled:
            self._db_flush_scheduled = True
            QTimer.singleShot(0, self._flush_db)

    def _flush_db(self):
        self._db_flush_scheduled = False
        if not self.db.encrypted:
            # Обычный режим: данные уже в файле, flush — дешёвый no-op.
            try:
                self.db.flush()
            except Exception as e:
                logging.warning("Не удалось сохранить БД на диск: %s", e)
            return
        if not self.db._dirty:
            return
        if self._write_busy:
            # Воркер занят — запишем свежий снимок сразу после текущей записи.
            self._write_pending = True
            return
        self._start_vault_write()

    def _start_vault_write(self, force=False):
        """Сделать снимок БД (в GUI-потоке) и отдать воркеру на шифрование+запись."""
        try:
            db_bytes = self.db.serialize_db()
        except Exception as e:
            logging.warning("Не удалось сериализовать БД: %s", e)
            return
        # Оптимистично считаем изменения «в работе»: новые правки снова поставят
        # _dirty и запланируют следующий flush. При сбое вернём _dirty=True.
        self.db._dirty = False
        self._write_busy = True
        self._write_pending = False
        self.statusBar().showMessage("Сохранение…")
        self._writer.submit(db_bytes, force)

    def _on_vault_written(self, ok, err, conflict):
        """Завершение фоновой записи (в GUI-потоке)."""
        self._write_busy = False
        if ok:
            self.statusBar().showMessage("Сохранено.", 1500)
            # Появились правки во время записи — пишем свежий снимок.
            if self._write_pending or self.db._dirty:
                self._start_vault_write()
        else:
            # Запись не удалась — данные снова считаем несохранёнными.
            self.db._dirty = True
            if conflict:
                if theme.themed_confirm(
                    self.config, self, "Файл изменён извне",
                    "Файл базы изменён другой программой с момента открытия.\n"
                    "Перезаписать его своими данными?",
                ):
                    self._start_vault_write(force=True)
                else:
                    self.statusBar().showMessage(
                        "Сохранение отменено: файл изменён извне.", 5000)
            else:
                logging.warning("Не удалось сохранить БД на диск: %s", err)
                self.statusBar().showMessage("ОШИБКА СОХРАНЕНИЯ!", 5000)
                theme.themed_info(
                    self.config, self, "Ошибка сохранения",
                    f"Не удалось сохранить базу на диск:\n{err}\n\n"
                    "Изменения остаются в памяти. Освободите место/проверьте "
                    "доступ к файлу и повторите.",
                )
        # Разбудить ожидающий close/lock, если воркер освободился.
        if self._writer_idle_loop is not None and not self._write_busy:
            self._writer_idle_loop.quit()

    def _wait_writer_idle(self, timeout_ms=15000):
        """Дождаться завершения текущей фоновой записи (для close/lock).
        Крутит локальный event-loop, поэтому done доставляется и UI не виснет."""
        if not self._write_busy:
            return
        loop = QEventLoop()
        self._writer_idle_loop = loop
        QTimer.singleShot(timeout_ms, loop.quit)   # страховочный таймаут
        loop.exec()
        self._writer_idle_loop = None

    def _shutdown_writer(self):
        """Корректно остановить поток фоновой записи (идемпотентно)."""
        thread = getattr(self, "_writer_thread", None)
        if thread is not None and thread.isRunning():
            self._wait_writer_idle()
            thread.quit()
            thread.wait(3000)

    # ─── Буфер обмена ────────────────────────────────────────────────────────

    def _on_field_copied(self):
        secs = self.config.get("clipboard_clear_secs", 0)
        if secs > 0:
            self.statusBar().showMessage(
                f"СКОПИРОВАНО!  (буфер очистится через {secs} сек.)", 3000
            )
            self._clip_timer.start(secs * 1000)
        else:
            self.statusBar().showMessage("СКОПИРОВАНО!", 2000)

    def _clear_clipboard(self):
        QApplication.clipboard().clear()
        self.statusBar().showMessage("Буфер обмена очищен.", 2000)

    # ─── Idle-блокировка ─────────────────────────────────────────────────────

    _ACTIVITY_EVENTS = (QEvent.Type.MouseMove, QEvent.Type.KeyPress,
                        QEvent.Type.MouseButtonPress, QEvent.Type.Wheel)

    def eventFilter(self, obj, ev):
        # Активность в любом окне программы (главное, настройки, диалоги)
        # сбрасывает счётчик простоя. Пока идёт повторная разблокировка —
        # активность не учитываем (иначе ввод пароля «продлевал» бы сессию).
        if ev.type() in self._ACTIVITY_EVENTS and not self._unlocking:
            self._last_activity = QDateTime.currentDateTime()
        if ev.type() == QEvent.Type.KeyPress and not self._unlocking:
            if self._maybe_handle_cyrillic_shortcut(ev):
                return True
        return super().eventFilter(obj, ev)

    def _maybe_handle_cyrillic_shortcut(self, ev):
        """Хоткеи с буквами записаны латиницей, но должны срабатывать и на
        русской раскладке. Обычные QShortcut ловят только латиницу; здесь по
        физической клавише (nativeVirtualKey, не зависит от раскладки) находим
        латинскую букву и запускаем нужное действие. Возвращает True, если
        сочетание перехвачено."""
        mods = ev.modifiers()
        # Интересуют только сочетания с Ctrl/Alt/Meta (одиночные буквы — нет).
        if not (mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            return False
        # Не вмешиваемся, когда открыто модальное окно (настройки, захват
        # клавиши, диалоги) — там свои обработчики.
        if QApplication.activeModalWidget() is not None:
            return False
        key = ev.key()
        # Латинская раскладка → штатные QShortcut уже сработают, выходим.
        if Qt.Key_A <= key <= Qt.Key_Z:
            return False
        vk = ev.nativeVirtualKey()       # Windows VK для A–Z = 0x41–0x5A
        if not (0x41 <= vk <= 0x5A):
            return False
        mask = mods & (Qt.ControlModifier | Qt.ShiftModifier
                       | Qt.AltModifier | Qt.MetaModifier)
        seq = QKeySequence(int(mask.value) | vk).toString()
        for action_id, s in shortcuts.effective(self.config).items():
            if s and QKeySequence(s).toString() == seq:
                self._run_shortcut(action_id)
                return True
        return False

    def _check_idle(self):
        mins = self.config.get("idle_lock_mins", 0)
        if mins <= 0 or self._unlocking:
            return
        # В обычном режиме прячем только открытую карточку; в зашифрованном —
        # блокируем всю базу (снимаем ключ), даже если карточка не открыта.
        if not self.db.encrypted and self._current_account_id is None:
            return
        elapsed_secs = self._last_activity.secsTo(QDateTime.currentDateTime())
        if elapsed_secs >= mins * 60:
            # Если открыто модальное окно (например, настройки) — закрываем его,
            # чтобы блокировка сработала даже с открытыми настройками.
            modal = QApplication.activeModalWidget()
            if modal is not None and modal is not self:
                modal.reject()
            self._lock_screen()

    def _lock_screen(self):
        self._idle_timer.stop()
        self._show_placeholder()
        self._current_account_id = None
        self.is_editing = False
        self._update_status_info()
        if self.db.encrypted:
            self._lock_vault()
        else:
            self.statusBar().showMessage(
                "Данные скрыты (авто-блокировка по простою).", 4000)

    def _lock_vault(self):
        """Зашифрованный режим: снять ключ и потребовать повторный ввод пароля.
        Поддерживает тот же путь восстановления («забыли пароль»), что и старт."""
        # Дождаться фоновой записи, затем синхронно сохранить и закрыть БД из
        # памяти (ключ обнуляется). Если сохранение не удалось, lock() пробросит
        # исключение и НЕ обнулит ключ/БД — прерываем блокировку, чтобы не
        # потерять несохранённые данные.
        self._wait_writer_idle()
        try:
            self.db.lock()
        except VaultConflictError:
            if not theme.themed_confirm(
                self.config, self, "Файл изменён извне",
                "Файл базы был изменён другой программой с момента открытия.\n"
                "Перезаписать его своими данными?",
            ):
                return  # блокировку отменяем, данные остаются доступны
            try:
                self.db.lock(force=True)
            except Exception as e:
                logging.warning("Не удалось сохранить БД перед блокировкой: %s", e)
                theme.themed_info(
                    self.config, self, "Ошибка сохранения",
                    f"Не удалось сохранить базу на диск:\n{e}\n\n"
                    "Блокировка отменена, данные остались доступны.",
                )
                return
        except Exception as e:
            logging.warning("Не удалось сохранить БД перед блокировкой: %s", e)
            theme.themed_info(
                self.config, self, "Ошибка сохранения",
                f"Не удалось сохранить базу на диск:\n{e}\n\n"
                "Блокировка отменена, данные остались доступны. Освободите "
                "место/проверьте доступ к файлу и повторите.",
            )
            return
        self._edit_cache.clear()
        self._dirty_ids.clear()
        self.current_account_data = None
        self._unlocking = True
        try:
            ok = self._open_database(self)
        finally:
            self._unlocking = False
        if not ok:
            self.close()
            return
        self._reload_tree()
        self._last_activity = QDateTime.currentDateTime()
        self._idle_timer.start(self._idle_timer.interval() or 30000)
        self.statusBar().showMessage("База разблокирована.", 3000)

    # ─── Скриншот-защита ─────────────────────────────────────────────────────

    def _apply_screenshot_protect(self, enabled: bool):
        try:
            hwnd = int(self.winId())
            WDA_NONE = 0x00000000
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            ctypes.windll.user32.SetWindowDisplayAffinity(
                hwnd, WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
            )
        except Exception as e:
            logging.warning("SetWindowDisplayAffinity: %s", e)

    # ─── Удаление всех данных ────────────────────────────────────────────────

    def _wipe_all_data(self):
        self.db.wipe_all_data()
        # Также удаляем все бэкапы в выбранной папке.
        deleted = 0
        folder = self.config.get("backup_folder", "").strip()
        if folder:
            try:
                deleted = bk.delete_all_backups(folder)
            except Exception as e:
                logging.warning("Не удалось удалить бэкапы: %s", e)
        self._current_account_id = None
        self._edit_cache.clear()
        self._dirty_ids.clear()
        self.current_account_data = None
        self.is_editing = False
        self._any_db_changes = False
        self._show_placeholder()
        self._reload_tree()
        self._update_bin_button()
        msg = "Все данные удалены."
        if deleted:
            msg += f" Удалено бэкапов: {deleted}."
        self.statusBar().showMessage(msg, 4000)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())