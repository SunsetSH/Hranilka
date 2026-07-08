"""Главное окно программы (MainWindow): сборка UI из mixin-ов (дерево,
карточка, хром окна, хоткеи), открытие/восстановление БД, тема. Точка входа
и qasync-цикл — в hranilka/app.py (этап 6 реструктуризации)."""
import sys
import atexit
import logging
from PySide6.QtWidgets import (QApplication, QMainWindow, QSplitter, QLabel,
                               QVBoxLayout, QWidget, QHBoxLayout,
                               QPushButton, QLineEdit,
                               QComboBox, QAbstractItemView)
from PySide6.QtCore import Qt, QTimer, QDateTime
from PySide6.QtGui import QFont
from hranilka.data import backup as bk

from hranilka.config import Config
from hranilka.data.database import (Database, FutureSchemaError, PreMigrationBackupError,
                      VaultConflictError)
from hranilka.ui.vault_controller import VaultController
from hranilka.ui.chrome import WindowChromeMixin
from hranilka.ui.shortcuts_mixin import ShortcutsMixin
from hranilka.ui.account_card import AccountCardMixin
from hranilka.ui.tree import AccountTree, TreeMixin
from hranilka import instance_lock
from hranilka import util
from hranilka.paths import BASE_DIR
from hranilka.ui.dialogs import SettingsDialog, RecycleBinDialog, ExportDialog
from hranilka.ui.tabs import AccountTabs
from hranilka.ui.titlebar import TitleBar, ResizableContainer
from hranilka.ui import theme


class MainWindow(WindowChromeMixin, ShortcutsMixin, AccountCardMixin,
                 TreeMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = Config()
        self.setWindowTitle("ХРАНИЛКА")
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

        # Вся работа с файлом-БД (фоновая запись зашифрованного контейнера,
        # отложенный flush, монопольный гейт привилегированных операций) вынесена
        # в VaultController (Эпик 4). Окно отвечает только за UI; контроллер для
        # UI-побочек (статус, вопрос о конфликте) обращается к окну.
        self.vault = VaultController(self.db, self, self.config)

        if not self._open_database():
            # Пользователь выбрал «Выход» в окне разблокировки.
            self.vault.shutdown()
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
        # Поколение карточки: растёт при каждом переключении аккаунта. Async-загрузка
        # и async-сохранение сверяют свой gen с текущим — устаревший результат не
        # трогает UI/кеш чужого аккаунта (H6-01/H6-02).
        self._card_gen = 0
        self._card_busy = False              # идёт async-загрузка/сохранение карточки
        # id только что созданного аккаунта: его карточка после загрузки сразу
        # открывается в режиме правки (одноразовый флаг, см. _load_account_into_ui)
        self._edit_on_load_id = None

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

        # Обучение при первом запуске — после старта event loop (окно уже
        # видимо, разблокировка БД завершена выше по __init__).
        QTimer.singleShot(0, self._maybe_show_welcome)

    def _maybe_show_welcome(self):
        """Показывает обучение при первом запуске (welcome_shown=False).

        Флаг ставится ДО показа: «увидел один раз — больше не навязываемся»,
        даже если программа закрылась во время обучения. Показ через open()
        (window-modal, неблокирующий) — блокирующий exec() в стартовой
        последовательности повесил бы qasync-цикл и тесты с processEvents.
        Повторный показ — из настроек (Поведение → «Показать обучение»)."""
        from hranilka.ui import welcome as ui_welcome
        if not ui_welcome.should_show(self.config):
            return
        self.config.set("welcome_shown", True)
        self.config.save()
        dlg = ui_welcome.WelcomeDialog(self.config, self)
        dlg.open()

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

        # H7-01: сначала дождаться in-flight run_async-мутаторов (барьер executor'а)
        # и запустить flush — иначе последний коммит из фонового потока мог бы
        # случиться уже ПОСЛЕ wait_idle и остаться несохранённым при закрытии.
        if not self.db.wait_executor_idle():
            theme.themed_info(
                self.config, self, "Операции не завершены",
                "Фоновые операции с базой ещё идут и не завершились вовремя.\n"
                "Подождите несколько секунд и закройте окно повторно.",
            )
            event.ignore()
            return
        self.vault.flush()

        # Дождаться завершения фоновой записи, чтобы дальнейшее синхронное
        # сохранение/бэкап не конкурировали с воркером за один файл. Если запись
        # не завершилась вовремя — НЕ закрываемся (иначе закрытие БД и снятие
        # instance-lock конкурировали бы с живым воркером): просим повторить.
        if not self.vault.wait_idle():
            theme.themed_info(
                self.config, self, "Сохранение не завершено",
                "Фоновое сохранение базы ещё идёт и не завершилось вовремя.\n"
                "Подождите несколько секунд и закройте окно повторно.",
            )
            event.ignore()
            return

        # Авто-бэкап при закрытии (если включён и были изменения)
        if self.config.get("backup_auto_on_close") and self.config.get("backup_folder"):
            had_changes = bool(unsaved) or self._any_db_changes
            if had_changes:
                # Бэкап копирует файл с диска — сначала сбросить отложенные
                # изменения (актуально для шифрованного режима). Ошибку flush
                # здесь не глотаем «вне try» (M3-02): если сбросить не удалось,
                # бэкап не делаем (он был бы устаревшим), а сам отказ сохранения
                # будет показан ниже в self.db.close() с диалогом.
                flushed = True
                try:
                    self.db.flush()
                except Exception as e:
                    flushed = False
                    logging.warning("Не удалось сбросить изменения перед бэкапом: %s", e)
                if flushed:
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

        # Гасим незавершённый async карточки/галереи ДО закрытия БД: висящие
        # загрузки/предпросмотр не должны обращаться к уже закрытому соединению.
        self._quiesce_card_async()
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
        self.vault.shutdown()
        # Остановить поток-исполнитель async-операций БД (после vault.shutdown —
        # к этому моменту фоновых записей/чтений уже нет).
        self.db.shutdown_executor()
        self._instance_lock.release()
        super().closeEvent(event)

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

        # Индикатор загрузки картинки в галерею (статус-бар)
        self.tabs.f_gallery_widget.upload_status_changed.connect(
            self._on_gallery_upload_status)

        # Постоянный индикатор в статус-баре (путь + режим)
        self.status_info = QLabel("")
        self.statusBar().addPermanentWidget(self.status_info)

        # Подключаем ВСЕ сигналы копирования к статус-бару
        copy_fields = [
            self.tabs.f_name, self.tabs.f_url, self.tabs.f_login, self.tabs.f_password,
            self.tabs.f_mobile, self.tabs.f_first, self.tabs.f_last, self.tabs.f_middle,
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
        self.tabs.gen_pass_cfg_btn.clicked.connect(
            self.open_password_generator_settings)
        self.tabs.gen_pd_btn.clicked.connect(self.generate_personal_data)

        # Горячие клавиши (после создания дерева и поля поиска — они нужны как
        # цели для контекстных шорткатов Del/Esc)
        self._setup_shortcuts()

    def apply_appearance(self):
        """Только визуальная часть (шрифт, стили). Лёгкая — подходит для живого
        предпросмотра настроек, не трогает геометрию/таймеры/БД.

        Повторное применение с теми же значениями пропускаем: setStyleSheet на
        главном окне вызывает полный repolish дерева (сотни элементов) и перегенерацию
        иконок-стрелок — это давало фриз при «Применить», когда менялись НЕ внешние
        настройки. Живой предпросмотр по-прежнему работает: там значения меняются,
        сигнатура отличается и стиль применяется."""
        font_name = self.config.get("font", "Cascadia Code")
        font_size = self.config.get("font_size", 14)
        text_color = self.config.get("text_color", "#FFFFFF")
        tree_bg = self.config.get("tree_bg_color", "#0000AA")
        main_bg = self.config.get("main_bg_color", "#0000AA")

        signature = (font_name, font_size, text_color, tree_bg, main_bg)
        if getattr(self, "_appearance_sig", None) == signature:
            return                              # внешний вид не менялся — repolish не нужен
        self._appearance_sig = signature

        self.setFont(QFont(font_name, font_size))
        # Общий стиль окна (вкл. кнопки-вкладки QPushButton[tabButton], спинбоксы, комбобоксы)
        self.setStyleSheet(theme.main_stylesheet(self.config))
        self.tabs.apply_scroll_bg(main_bg)
        self.tree.setStyleSheet(f"""
            QTreeWidget {{ border: 2px inset #808080; background-color: {tree_bg}; color: {text_color}; font-family: '{font_name}'; font-size: {font_size}px; }}
            QTreeWidget::item {{ padding: 4px; border: 1px solid transparent; }}
            QTreeWidget::item:hover {{ background-color: {main_bg}; }}
            QTreeWidget::item:selected {{ background-color: {text_color}; color: {tree_bg}; }}
        """ + self._branch_arrow_css(text_color, tree_bg, main_bg))

    def _branch_arrow_css(self, text_color, tree_bg, main_bg):
        """Стрелки сворачивания/разворачивания, перекрашенные под тему.

        Стандартные стрелки рисуются стилем ОС фиксированным цветом и теряются
        на выделении (фон строки = text_color). Генерируем свои треугольники:
        в обычном состоянии — цветом текста, на выделении — цветом фона дерева
        (как инвертируется текст), чтобы стрелка всегда оставалась видимой.

        QSS в этой версии Qt не рендерит image:url() с data:-URI (проверено
        офскрин-рендером — картинка тихо не показывается, стрелки пропадают).
        Поэтому пишем PNG-файлы во временный каталог, но только когда цвета
        реально меняются (кэш по паре text_color/tree_bg, M-17) — не на каждый
        apply_appearance. Каталог создаётся один раз за запуск и удаляется
        через atexit."""
        cache_key = (text_color, tree_bg)
        cached = getattr(self, "_branch_css_cache", None)
        if cached is not None and cached[0] == cache_key:
            body = cached[1]
        else:
            body = self._render_branch_arrow_files(text_color, tree_bg)
            self._branch_css_cache = (cache_key, body)
        # Заливка области стрелки (background-color) зависит от main_bg (hover),
        # поэтому её оставляем вне кэша по цветам стрелок — она дешёвая (текст).
        return f"""
            QTreeWidget::branch {{ background-color: {tree_bg}; }}
            QTreeWidget::branch:hover {{ background-color: {main_bg}; }}
            QTreeWidget::branch:selected {{ background-color: {text_color}; }}
        """ + body

    def _branch_icon_dir(self):
        """Временный каталог для PNG стрелок дерева; создаётся один раз за
        запуск и удаляется целиком при выходе (atexit)."""
        d = getattr(self, "_branch_icon_dir_path", None)
        if d is None:
            import tempfile
            d = tempfile.mkdtemp(prefix="hranilka-arrows-")
            self._branch_icon_dir_path = d
            atexit.register(self._cleanup_branch_icon_dir)
        return d

    def _cleanup_branch_icon_dir(self):
        d = getattr(self, "_branch_icon_dir_path", None)
        if d:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def _render_branch_arrow_files(self, text_color, tree_bg):
        """Отрисовать 4 стрелки в файлы (перезаписывая прежние для этих же
        имён) и вернуть QSS-правила с путями к ним."""
        from PySide6.QtGui import QPixmap, QPainter, QPolygon, QColor
        from PySide6.QtCore import QPoint
        import os

        icon_dir = self._branch_icon_dir()

        def make(direction, color, name):
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
            path = os.path.join(icon_dir, name)
            pm.save(path, "PNG")
            # QSS ожидает прямые слэши даже на Windows.
            return path.replace("\\", "/")

        cn = make("closed", text_color, "cn.png")
        op = make("open", text_color, "op.png")
        cs = make("closed", tree_bg, "cs.png")
        ops = make("open", tree_bg, "ops.png")
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

    def export_all(self):
        """Экспорт всей базы — то же, что кнопка «Экспортировать всё» в настройках."""
        tree = self.db.export_subtree()
        if not tree:
            self.statusBar().showMessage("Нечего экспортировать", 3000)
            return
        ExportDialog(self.config, tree, "Вся база", self).exec()

    def open_settings(self):
        dialog = SettingsDialog(self.config, self)
        dialog.set_db_path(self.db.db_path)
        dialog.set_db(self.db)
        # Привилегированные операции (шифрование/бэкап/восстановление) — только
        # монопольно, без конкуренции с фоновым воркером записи (H3-01).
        dialog.set_vault_runner(self.vault.run_exclusive)
        dialog.appearance_changed.connect(self.apply_appearance)  # лёгкий предпросмотр
        dialog.settings_applied.connect(self.apply_config)        # полное применение
        dialog.settings_applied.connect(self._rebind_shortcuts)   # пере-привязка хоткеев
        # Восстановление из бэкапа: всю последовательность (закрыть БД → заменить
        # файл → переоткрыть → обновить UI) выполняет MainWindow, диалог лишь
        # запрашивает её и показывает результат (см. _restore_from_backup).
        dialog.set_restore_handler(self._restore_from_backup)
        dialog.exec()
        if dialog.delete_all_confirmed:     # публичное свойство (L-10)
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
            self.vault.shutdown()
            self._instance_lock.release()
            sys.exit(1)
        except PreMigrationBackupError as e:
            # Не удалось создать резервную копию перед необратимой правкой схемы
            # (H7-03): база НЕ тронута. Открытие прерываем с понятным сообщением,
            # чтобы пользователь освободил место/проверил доступ и повторил.
            logging.error("Отказ открытия: %s", e)
            theme.themed_info(
                self.config, self, "Не удалось подготовить базу", str(e),
            )
            self.vault.shutdown()
            self._instance_lock.release()
            sys.exit(1)

    @staticmethod
    def _read_container_async(path):
        """Запустить чтение файла-контейнера в отдельном потоке и вернуть Future
        (H-9). В __init__ event-loop qasync ещё не крутится, поэтому используем
        ThreadPoolExecutor, а не loop.run_in_executor. Пул закрывается сам после
        завершения задачи (shutdown(wait=False))."""
        import concurrent.futures

        def _read():
            with open(path, "rb") as f:
                return f.read()

        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(_read)
        ex.shutdown(wait=False)                  # не ждём здесь — Future отдаёт результат
        return fut

    def _open_database(self, parent=None):
        """Открыть СОЕДИНЕНИЕ с БД: зашифрованную — через ввод мастер-пароля/
        recovery-кода, обычную — напрямую. При утере секрета пользователь может
        восстановить бэкап или начать с чистой базы. Возвращает True при успехе,
        False если пользователь выбрал «Выход». Источник истины о шифровании —
        сигнатура файла.

        ВАЖНО: только открывает соединение и НЕ валидирует/не мигрирует схему —
        это делает отдельный шаг (_validate_schema_or_exit при старте/lock либо
        _open_and_validate_after_restore с откатом при restore). Так один и тот же
        опенер годится и для сценариев, где ошибку схемы нужно откатить, а не
        завершать программу (H65-05)."""
        from hranilka.crypto import store as cs
        from hranilka.ui.dialogs import UnlockDialog
        while True:
            if not cs.is_encrypted_file(self.db.db_path):
                self.db.connect()
                self.config.set("encryption_enabled", False)
                self.config.save()
                return True

            # Чтение зашифрованного контейнера (может быть крупным) выносим в
            # фоновый поток и запускаем ДО построения диалога (H-9/M7-06). Future
            # передаём в сам диалог: он показывается МОМЕНТАЛЬНО, а тяжёлое чтение
            # файла перекрывается вводом пароля. Байты берутся лениво, лишь при
            # первой попытке разблокировки (там же всплывёт OSError чтения). На
            # повторной итерации цикла создаётся свежий future.
            reader = self._read_container_async(self.db.db_path)
            dlg = UnlockDialog(self.config, b"", parent)
            dlg.set_container_future(reader)
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
                # Через restore_backup (а не прямой copy2): кандидат проверяется
                # до и после замены, есть откат при сбое (H3-02). Текущий файл
                # уже отложен (_archive_db_file), поэтому терять нечего.
                try:
                    bk.restore_backup(dlg.restore_path, self.db.db_path)
                except Exception as e:
                    theme.themed_info(self.config, parent, "Ошибка",
                                      f"Не удалось восстановить бэкап:\n{e}")
                # Повторяем цикл: восстановленный файл может быть как обычным,
                # так и зашифрованным (другим паролем) — тогда снова спросим.
                continue

            return False  # «Выход»

    def _open_and_validate_after_restore(self):
        """Открыть переоткрытую БД И провалидировать/мигрировать её схему (H65-05).

        Plaintext и encrypted проходят один путь: после открытия соединения
        выполняется create_tables() (миграции, обязательные таблицы, отказ при
        схеме новее поддерживаемой — FutureSchemaError). Возвращает True только
        если БД открыта и схема валидна; при любой ошибке закрывает БД без записи
        и возвращает False — вызыватель откатывается к прежнему vault. Так
        восстановленный encrypted-файл больше не публикуется для чтения/записи без
        миграции и проверки версии схемы."""
        if not self._open_database(self):
            return False
        try:
            self.db.create_tables()
        except FutureSchemaError as e:
            logging.error("Восстановленный файл: несовместимая схема: %s", e)
            theme.themed_info(self.config, self, "Несовместимая версия базы", str(e))
            self.db.close(persist=False)
            return False
        except Exception as e:                       # noqa: BLE001
            logging.error("Восстановленный файл не прошёл проверку схемы: %s",
                          e, exc_info=e)
            theme.themed_info(self.config, self, "Ошибка базы",
                              f"Восстановленный файл не прошёл проверку схемы:\n{e}")
            self.db.close(persist=False)
            return False
        return True

    def _restore_from_backup(self, path):
        """Восстановление из бэкапа (вызывается из «Настроек»). Возвращает
        (ok, err) для показа в диалоге.

        Критично для plaintext-режима (Баг 1, WinError 5): SQLite держит файл
        hranilka.db открытым, и `os.replace` внутри restore_backup на Windows
        падает. Поэтому соединение закрываем ДО замены файла. Закрываем БЕЗ
        сохранения — иначе текущая БД затёрла бы восстановленный файл. После
        замены переоткрываем БД (восстановленный файл может оказаться
        зашифрованным — тогда _open_database спросит пароль)."""
        import os
        from pathlib import Path
        # H7-01: перед закрытием БД гарантируем, что последние изменения попали в
        # ФАЙЛ (иначе restore незаметно потерял бы их в шифр. режиме). Порядок важен:
        #   1) барьер executor'а — дожидаемся всех in-flight run_async-мутаторов; они,
        #      закоммитив, помечают БД грязной (иначе коммит мог бы случиться уже
        #      ПОСЛЕ wait_idle и остался бы несохранённым);
        #   2) синхронный flush — запускает запись, если БД грязная (в т.ч. когда
        #      сброс был лишь запланирован через QTimer, но ещё не стартовал);
        #   3) wait_idle — дожидаемся завершения самой фоновой записи.
        if not self.db.wait_executor_idle():
            return False, ("Фоновые операции с базой не завершились вовремя.\n"
                           "Повторите попытку через несколько секунд.")
        self.vault.flush()
        if not self.vault.wait_idle():
            return False, ("Фоновое сохранение базы не завершилось вовремя.\n"
                           "Повторите попытку через несколько секунд.")
        # Гасим незавершённый async карточки/галереи ДО закрытия БД (H65-02).
        self._quiesce_card_async()
        self.db.close(persist=False)
        db_path = self.db.db_path
        # Страховочная копия текущей БД (durable: fsync содержимого). Восстановленный
        # encrypted-файл проверяется лишь структурно (_is_valid_db), а подлинность
        # (GCM), пароль и версия схемы — только при открытии. Если открыть/проверить
        # не удалось, вернём прежнюю БД из этой копии.
        rollback = None
        if os.path.exists(db_path):
            rollback = db_path + ".pre-restore"
            try:
                bk._copy_durable(Path(db_path), Path(rollback))
            except OSError as e:
                # H65-03: рабочий файл есть, но страховочную копию создать не удалось.
                # Продолжать restore нельзя — при неоткрытии кандидата рабочая БД
                # пропала бы безвозвратно. Отменяем restore и возвращаем прежнюю БД.
                logging.error("Не удалось создать страховочную копию перед restore: %s", e)
                self._discard_file(rollback)
                if self._open_and_validate_after_restore():
                    self._after_db_reopened(None)
                    return False, ("Не удалось создать страховочную копию; "
                                   f"восстановление отменено:\n{e}")
                self.close()
                return False, f"Не удалось создать страховочную копию: {e}"
        try:
            bk.restore_backup(path, db_path)
        except Exception as e:
            # restore_backup при сбое откатывает файл к прежнему состоянию —
            # переоткрываем БД как была и сообщаем об ошибке.
            self._discard_file(rollback)
            if not self._open_and_validate_after_restore():
                self.close()
                return False, str(e)
            self._after_db_reopened(None)
            return False, str(e)
        if not self._open_and_validate_after_restore():
            # Восстановленный файл не открылся/не аутентифицирован/не прошёл
            # проверку схемы. Возвращаем прежнюю БД из страховочной копии.
            if self._rollback_restore(rollback, db_path):
                return False, ("Восстановленный файл не удалось открыть или проверить; "
                               "возвращена прежняя база.")
            self.close()
            return False, "Восстановленный файл не удалось открыть или проверить."
        self._discard_file(rollback)
        self._after_db_reopened("База данных восстановлена из бэкапа.")
        return True, None

    def _discard_file(self, path):
        """Тихо удалить временную страховочную копию БД (.pre-restore), если она
        есть. Это ПОЛНАЯ копия базы (все пароли/BLOB в открытом виде), поэтому
        затираем содержимое нулями перед удалением, а не просто unlink (H-3)."""
        import os
        if path and os.path.exists(path):
            util.best_effort_wipe(path)

    def _rollback_restore(self, rollback, db_path):
        """Вернуть прежнюю БД из страховочной копии после неудачного восстановления.
        Возвращает True, если прежняя база возвращена, открыта и провалидирована."""
        import os
        if not rollback or not os.path.exists(rollback):
            return False
        try:
            os.replace(rollback, db_path)
        except OSError as e:
            logging.warning("Не удалось вернуть прежнюю БД: %s", e)
            return False
        if not self._open_and_validate_after_restore():
            return False
        self._after_db_reopened("Восстановление отменено: возвращена прежняя база.")
        return True

    def _after_db_reopened(self, message):
        """Сброс состояния и UI после переоткрытия БД (восстановление бэкапа)."""
        self._current_account_id = None
        self._edit_cache.clear()
        self._dirty_ids.clear()
        self.current_account_data = None
        self.is_editing = False
        self._any_db_changes = False
        self._show_placeholder()
        self._reload_tree()
        self._update_bin_button()
        if message:
            self.statusBar().showMessage(message, 3000)

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