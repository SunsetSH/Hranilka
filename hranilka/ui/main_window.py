"""Главное окно программы (MainWindow): сборка UI из mixin-ов (дерево,
карточка, хром окна, хоткеи), открытие/восстановление БД, тема. Точка входа
и qasync-цикл — в hranilka/app.py (этап 6 реструктуризации)."""
import sys
import atexit
import logging
from collections import Counter
from PySide6.QtWidgets import (QApplication, QMainWindow, QSplitter, QLabel,
                               QVBoxLayout, QWidget, QHBoxLayout,
                               QPushButton, QLineEdit, QStackedWidget,
                               QComboBox, QAbstractItemView)
from PySide6.QtCore import Qt, QTimer, QDateTime
from PySide6.QtGui import QFont
from hranilka.services import backup as bk

from hranilka.core.config import Config
from hranilka.data.database import (Database, FutureSchemaError, PreMigrationBackupError,
                      VaultConflictError)
from hranilka.ui.vault_controller import VaultController
from hranilka.ui.chrome import WindowChromeMixin
from hranilka.ui.shortcuts_mixin import ShortcutsMixin
from hranilka.ui.account_card import AccountCardMixin
from hranilka.ui.fin_card import FinCardMixin
from hranilka.ui.server_card import ServerCardMixin
from hranilka.ui.tree import AccountTree, TreeMixin
from hranilka.core import instance_lock
from hranilka.core import util
from hranilka.core.nodetypes import ACCOUNT, FIN_LEAF_TYPES, SERVER
from hranilka.core.fin_types import FIN_TYPES
from hranilka.core.paths import BASE_DIR
from hranilka.ui.dialogs import SettingsDialog, RecycleBinDialog, ExportDialog
from hranilka.ui.tabs import AccountTabs
from hranilka.ui.fin_tabs import FinItemTabs
from hranilka.ui.server_tabs import ServerTabs
from hranilka.ui.titlebar import TitleBar, ResizableContainer
from hranilka.ui import theme


class MainWindow(WindowChromeMixin, ShortcutsMixin, ServerCardMixin, FinCardMixin,
                 AccountCardMixin, TreeMixin, QMainWindow):
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
        # Открытая фин-запись правой панели: (node_type, id) | None. Аккаунт и
        # запись не открыты одновременно (одна правая панель).
        self._current_fin = None
        self.current_fin_data = None
        # id только что созданной записи: карточка после загрузки открывается в
        # правке (одноразовый флаг (node_type, id), см. _load_fin_into_ui).
        self._fin_edit_on_load = None
        # Открытый сервер правой панели: (SERVER, id) | None — независимо от
        # _current_fin (docs/ТЗ_VPS_Серверы.md §2, ServerCardMixin).
        self._current_server = None
        self.current_server_data = None
        # id только что созданного сервера: карточка после загрузки открывается
        # в правке (одноразовый флаг, см. _load_server_into_ui).
        self._server_edit_on_load = None
        # Ключ несохранённых правок — кортеж (node_type, id): id-пространства
        # аккаунтов и фин-записей раздельны, голый id их бы столкнул.
        self._edit_cache = {}                # (type, id) -> {"storage":..., "links":[...]}
        self._dirty_ids = set()              # {(type, id)} — записи с несохранёнными правками
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
        dlg = ui_welcome.WelcomeDialog(
            self.config, self, self._apply_welcome_fin_instruments,
            self._apply_welcome_recycle_bin, self._apply_welcome_servers)
        dlg.open()

    def _apply_welcome_fin_instruments(self, show: bool) -> bool:
        """Применить выбор фин-инструментов из приветственного обучения.

        Отключение использует те же гарантии, что и вкладка «Опции»: записи
        остаются в базе, но пользователь подтверждает скрытие, если они есть.
        Возвращаем False, чтобы финальный слайд остался открытым при отказе.
        """
        old = self.config.get("show_fin_instruments", False)
        if old == show:
            return True
        if old and not show and self.db.count_fin_items() > 0:
            if not theme.themed_confirm(
                    self.config, self, "Скрыть финансовые инструменты",
                    "Фин-инструменты будут скрыты из интерфейса (дерево, связи, "
                    "экспорт, создание). Записи останутся в БД, корзина продолжит "
                    "их показывать. Несохранённые правки фин-записей будут "
                    "сброшены. Продолжить?"):
                return False
        self.config.set("show_fin_instruments", show)
        self.config.save()
        self.apply_config()
        return True

    def _apply_welcome_recycle_bin(self, enabled: bool) -> bool:
        """Сохранить и сразу применить выбор корзины из обучения."""
        self.config.set("recycle_bin_enabled", enabled)
        self.config.save()
        self.apply_config()
        return True

    def _apply_welcome_servers(self, show: bool) -> bool:
        """Применить выбор VPS-серверов из приветственного обучения.

        Симметрично _apply_welcome_fin_instruments/_resolve_show_servers
        (settings/dialog.py): те же гарантии при отключении, если записи
        уже есть. Возвращаем False, чтобы финальный слайд остался открытым
        при отказе.
        """
        old = self.config.get("show_servers", False)
        if old == show:
            return True
        if old and not show and self.db.count_servers() > 0:
            if not theme.themed_confirm(
                    self.config, self, "Скрыть серверы",
                    "Серверы будут скрыты из интерфейса (дерево, связи, "
                    "создание). Записи останутся в БД, корзина продолжит их "
                    "показывать. Несохранённые правки серверов будут "
                    "сброшены. Продолжить?"):
                return False
        self.config.set("show_servers", show)
        self.config.save()
        self.apply_config()
        return True

    # Подписи типизированного подсчёта несохранённых записей в диалоге закрытия.
    # Фин-часть строится из реестра (spec.unsaved_label) — новый тип получает
    # свою строку подсчёта автоматически, без правки MainWindow.
    _UNSAVED_LABELS = ((ACCOUNT, "Аккаунтов"),) + tuple(
        (spec.node_type, spec.unsaved_label) for spec in FIN_TYPES.values()) + (
        (SERVER, "Серверов"),)

    def _unsaved_keys(self):
        """Ключи (node_type, id) записей с несохранёнными правками, включая
        редактируемую сейчас (аккаунт или фин-запись — открыта одна из них)."""
        unsaved = set(self._dirty_ids)
        if self.is_editing and self._current_account_id is not None:
            unsaved.add((ACCOUNT, self._current_account_id))
        if self.is_editing and self._current_fin is not None:
            unsaved.add(self._current_fin)
        if self.is_editing and self._current_server is not None:
            unsaved.add(self._current_server)
        return unsaved

    def _unsaved_summary(self, unsaved):
        """Текст диалога закрытия: типизированный подсчёт (только ненулевые)."""
        counts = Counter(node_type for node_type, _id in unsaved)
        lines = [f"{label}: {counts[t]}" for t, label in self._UNSAVED_LABELS
                 if counts[t]]
        return "Есть несохранённые изменения.\n" + "\n".join(lines)

    def _save_unsaved_before_exit(self):
        """«Сохранить и выйти»: снести текущую карточку в кеш (общий stash) и
        синхронно записать все черновики _edit_cache в БД (методы под RLock;
        wait_executor_idle идёт следом по существующему коду закрытия).
        True — всё записано (кеш очищен); False — ошибка (показана, не выходим)."""
        if self.is_editing and self._current_fin is not None:
            self._stash_current_fin_edits()
        elif self.is_editing and self._current_server is not None:
            self._stash_current_server_edits()
        elif self.is_editing and self._current_account_id is not None:
            self._stash_current_edits(self._current_account_id)
        show_fin = self.config.get("show_fin_instruments", False)
        show_servers = self.config.get("show_servers", False)
        for (node_type, rec_id), cached in list(self._edit_cache.items()):
            # Защита: при выключенной опции фин-правок/серверов в кеше быть не
            # должно (сброшены при выключении) — пропускаем, не пишем вслепую.
            if node_type in FIN_LEAF_TYPES and not show_fin:
                continue
            if node_type == SERVER and not show_servers:
                continue
            try:
                if node_type == ACCOUNT:
                    self.db.save_account_with_links(
                        rec_id, cached["storage"], cached.get("links") or [],
                        cached.get("fin_links") if show_fin else None,
                        cached.get("server_links") if show_servers else None)
                elif node_type in FIN_LEAF_TYPES:
                    # links=None (H-02: ошибка чтения при осиротевшей загрузке
                    # галереи) передаётся КАК ЕСТЬ, без "or []" — save_fin_item_
                    # with_links трактует None как «не трогать fin_links»,
                    # иначе автосохранение при выходе стёрло бы реальные связи.
                    self.db.save_fin_item_with_links(
                        rec_id, cached["storage"], cached.get("links"))
                elif node_type == SERVER:
                    self.db.save_server_with_links(
                        rec_id, cached["storage"], cached.get("links"))
            except Exception as e:                   # noqa: BLE001 — показать и не выходить
                storage = cached.get("storage") or {}
                name = (storage.get("fields", {}).get("account_name")
                        if node_type == ACCOUNT else storage.get("name")) or "?"
                logging.error("Не удалось сохранить запись «%s» при выходе: %s",
                              name, e, exc_info=e)
                theme.themed_info(
                    self.config, self, "Ошибка сохранения",
                    f"Не удалось сохранить запись «{name}».\n"
                    f"Выход отменён. Подробности — в логе программы.")
                return False
        self._any_db_changes = True
        self._dirty_ids.clear()
        self._edit_cache.clear()
        self.is_editing = False
        return True

    def closeEvent(self, event):
        # Записи с несохранёнными правками — типизированный подсчёт и три
        # варианта: сохранить и выйти / выйти без сохранения / вернуться.
        unsaved = self._unsaved_keys()
        if unsaved and self.config.get("warn_on_exit_unsaved", True):
            choice = theme.themed_choice(
                self.config, self, "Несохранённые данные",
                self._unsaved_summary(unsaved),
                ["Сохранить и выйти", "Выйти", "Вернуться"])
            if choice is None or choice == 2:        # Esc/крестик = «Вернуться»
                event.ignore()
                return
            if choice == 0 and not self._save_unsaved_before_exit():
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

        # Ряд 1 + ряд 2: состав каждого ряда зависит от тумблеров show_fin_instruments/
        # show_servers и перестраивается в _relayout_create_buttons (вызывается
        # при старте и из apply_config). Кнопки создаются один раз здесь;
        # relayout только перекладывает их между row1_layout/row2_layout —
        # ссылки на кнопки (add_account_btn и т.д.) не дублируются.
        self.row1_layout = QHBoxLayout()
        self.add_folder_btn = QPushButton(" + ПАПКА ")
        self.add_service_btn = QPushButton(" + СЕРВИС ")
        self.add_account_btn = QPushButton(" + АККАУНТ ")
        self.add_folder_btn.clicked.connect(self.add_folder)
        self.add_service_btn.clicked.connect(self.add_service)
        self.add_account_btn.clicked.connect(self.add_account)
        left_layout.addLayout(self.row1_layout)

        self.row2_widget = QWidget()
        self.row2_layout = QHBoxLayout(self.row2_widget)
        self.row2_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.row2_widget)

        # Кнопки создания фин-записей из реестра FIN_TYPES (короткая подпись
        # spec.short_title) — сгруппированы в свой контейнер (findChildren в
        # тестах опирается на то, что здесь только фин-кнопки).
        self.fin_buttons_widget = QWidget()
        fin_btn_layout = QHBoxLayout(self.fin_buttons_widget)
        fin_btn_layout.setContentsMargins(0, 0, 0, 0)
        for type_id, spec in FIN_TYPES.items():
            fin_btn = QPushButton(f" + {spec.short_title} ")
            fin_btn.clicked.connect(
                lambda checked=False, tid=type_id: self.add_fin_record(tid))
            fin_btn_layout.addWidget(fin_btn)

        # Кнопка создания VPS-сервера (docs/ТЗ_VPS_Серверы.md §4) — свой
        # контейнер, аналогично fin_buttons_widget.
        self.srv_buttons_widget = QWidget()
        srv_btn_layout = QHBoxLayout(self.srv_buttons_widget)
        srv_btn_layout.setContentsMargins(0, 0, 0, 0)
        self.add_server_btn = QPushButton(" + СЕРВЕР ")
        self.add_server_btn.clicked.connect(self.add_server)
        srv_btn_layout.addWidget(self.add_server_btn)

        self._relayout_create_buttons()

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

        # Плашка срока действия карты (над вкладками), скрыта по умолчанию.
        self.fin_banner = QLabel("")
        self.fin_banner.setProperty("heading", "true")
        self.fin_banner.setAlignment(Qt.AlignCenter)
        self.fin_banner.hide()
        right_layout.addWidget(self.fin_banner)

        self.placeholder_label = QLabel("\n\n[ ВЫБЕРИТЕ ЗАПИСЬ ИЗ ДЕРЕВА ]\n\n")
        self.placeholder_label.setProperty("heading", "true")
        self.placeholder_label.setAlignment(Qt.AlignCenter)

        self.tabs = AccountTabs(config=self.config)
        # Вкладки финансовых карточек — по FinItemTabs на тип из реестра (ключ —
        # node_type узла дерева: узел несёт только его, не type_id; уникальность
        # проверена при загрузке реестра — см. fin_types._check_unique_node_types).
        # fin_tabs — текущая открытая карточка, переключается в _open_fin_card;
        # до открытия первой карточки — любая (или None, если реестр пуст),
        # никогда не читается до присваивания в _open_fin_card.
        self.fin_tabs_by_type = {
            spec.node_type: FinItemTabs(spec, config=self.config)
            for spec in FIN_TYPES.values()}
        self.fin_tabs = next(iter(self.fin_tabs_by_type.values()), None)

        # Карточка VPS-сервера — независимый код (docs/ТЗ_VPS_Серверы.md §2):
        # один тип узла (SERVER), поэтому одна страница (не словарь, как у fin).
        self.server_tabs = ServerTabs(config=self.config)

        # Правая панель — стек: заглушка / карточка аккаунта / карточки записей.
        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(self.placeholder_label)
        self.right_stack.addWidget(self.tabs)
        for fin_tabs in self.fin_tabs_by_type.values():
            self.right_stack.addWidget(fin_tabs)
        self.right_stack.addWidget(self.server_tabs)
        self.right_stack.setCurrentWidget(self.placeholder_label)
        right_layout.addWidget(self.right_stack)

        # Связанные аккаунты: навигация и добавление
        self.tabs.f_linked.navigate_requested.connect(self.on_link_navigate)
        self.tabs.f_linked.add_requested.connect(self.on_add_link_requested)

        # Привязанные карты/кошельки на карточке аккаунта (§8)
        self.tabs.f_fin_linked.navigate_requested.connect(self.on_fin_link_navigate)
        self.tabs.f_fin_linked.add_requested.connect(self.on_add_fin_link_requested)

        # Привязанные серверы на карточке аккаунта (docs/ТЗ_VPS_Серверы.md §4)
        self.tabs.f_server_linked.navigate_requested.connect(self.on_server_link_navigate)
        self.tabs.f_server_linked.add_requested.connect(self.on_add_server_link_requested)

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

        # Копируемые поля фин-карточек (вкл. секретные — автоочистка буфера
        # обязательна для всех). Итерация по дескрипторам, без ручных списков;
        # плюс сигналы вкладки «Связи» каждой карточки.
        for fin_tabs in self.fin_tabs_by_type.values():
            fin_tabs.f_name.copy_signal.connect(self._on_field_copied)
            for _key, widget in fin_tabs.fields():
                widget.copy_signal.connect(self._on_field_copied)
            for _key, widget in fin_tabs.list_fields():
                widget.copy_signal.connect(self._on_field_copied)
            fin_tabs.f_linked_accounts.navigate_requested.connect(
                self.on_link_navigate)
            fin_tabs.f_linked_accounts.add_requested.connect(
                self.on_add_fin_account_link_requested)
            # Индикатор загрузки картинки в галерею записи (статус-бар, §5).
            fin_tabs.f_gallery_widget.upload_status_changed.connect(
                self._on_gallery_upload_status)

        # Копируемые поля серверной карточки — независимо (docs/ТЗ_VPS_Серверы.md §2).
        self.server_tabs.f_name.copy_signal.connect(self._on_field_copied)
        for _key, widget in self.server_tabs.fields():
            widget.copy_signal.connect(self._on_field_copied)
        for _key, widget in self.server_tabs.list_fields():
            widget.copy_signal.connect(self._on_field_copied)
        # «Доп. IP» — список вне list_fields() (список строк, не словарей;
        # УИ §2026-07-15), но копирование через тот же канал автоочистки.
        self.server_tabs._extra_ips_widget.copy_signal.connect(
            self._on_field_copied)
        self.server_tabs.f_linked_accounts.navigate_requested.connect(
            self.on_server_account_link_navigate)
        self.server_tabs.f_linked_accounts.add_requested.connect(
            self.on_add_server_account_link_requested)
        self.server_tabs.f_gallery_widget.upload_status_changed.connect(
            self._on_gallery_upload_status)

        # КНОПКИ РЕДАКТИРОВАНИЯ — общие для аккаунта/фин-записи/сервера, роутинг
        # по типу открытого узла (edit/save/cancel_current в ServerCardMixin/
        # FinCardMixin).
        self.action_layout = QHBoxLayout()

        self.edit_btn = QPushButton(" РЕДАКТИРОВАТЬ ")
        self.edit_btn.clicked.connect(self.edit_current)

        self.save_btn = QPushButton(" СОХРАНИТЬ ")
        self.save_btn.clicked.connect(self.save_current)

        self.cancel_btn = QPushButton(" ОТМЕНА ")
        self.cancel_btn.clicked.connect(self.cancel_current)
        
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
        for fin_tabs in self.fin_tabs_by_type.values():
            fin_tabs.apply_scroll_bg(main_bg)
        self.server_tabs.apply_scroll_bg(main_bg)
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

        # Показ финансовых инструментов (кнопки ряда 2, секция карточки аккаунта,
        # дерево). При выключении — скрыть отовсюду, кроме корзины.
        self._apply_fin_visibility()

        # Показ VPS-серверов (docs/ТЗ_VPS_Серверы.md §4): кнопка создания +
        # секция карточки аккаунта + дерево. При выключении — скрыть отовсюду,
        # кроме корзины (по образцу _apply_fin_visibility).
        self._apply_server_visibility()

        # Раскладка рядов кнопок создания зависит от обоих тумблеров сразу —
        # пересчитывается один раз после того, как оба флага применены.
        self._relayout_create_buttons()

    def _relayout_create_buttons(self):
        """Перестроить состав рядов кнопок создания по флагам show_fin_instruments/
        show_servers (docs/ТЗ_VPS_Серверы.md §4). Кнопки не пересоздаются —
        только перекладываются между row1_layout/row2_layout:
        - фин + серверы:  ряд1 «+ПАПКА +СЕРВИС +АККАУНТ», ряд2 «+КАРТА +КРИПТО +СЕРВЕР»;
        - только фин:      ряд1 «+ПАПКА +СЕРВИС +АККАУНТ», ряд2 «+КАРТА +КРИПТО»;
        - только серверы:  ряд1 «+ПАПКА +СЕРВИС»,           ряд2 «+СЕРВЕР +АККАУНТ»;
        - оба выключены:   ряд1 «+ПАПКА +СЕРВИС +АККАУНТ», ряд2 скрыт целиком."""
        show_fin = self.config.get("show_fin_instruments", False)
        show_servers = self.config.get("show_servers", False)

        for layout in (self.row1_layout, self.row2_layout):
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    layout.removeWidget(w)

        if not show_fin and show_servers:
            row1 = [self.add_folder_btn, self.add_service_btn]
            row2 = [self.srv_buttons_widget, self.add_account_btn]
        else:
            row1 = [self.add_folder_btn, self.add_service_btn, self.add_account_btn]
            row2 = []
            if show_fin:
                row2.append(self.fin_buttons_widget)
            if show_servers:
                row2.append(self.srv_buttons_widget)

        # Стретч = число кнопок внутри виджета ряда (1 у одиночной кнопки,
        # N у контейнера вроде fin_buttons_widget/srv_buttons_widget). Без
        # этого QHBoxLayout делит излишек ширины ПОРОВНУ между виджетами
        # ряда независимо от того, сколько кнопок каждый содержит — при
        # растяжении окна «+ СЕРВЕР» (1 кнопка в своём контейнере) становился
        # шире «+ КАРТА»/«+ КРИПТО» (2 кнопки делят тот же излишек пополам).
        # Пропорциональный стретч выравнивает прирост на кнопку.
        for w in row1:
            self.row1_layout.addWidget(w, self._btn_group_stretch(w))
        for w in row2:
            self.row2_layout.addWidget(w, self._btn_group_stretch(w))

        self.fin_buttons_widget.setVisible(show_fin)
        self.srv_buttons_widget.setVisible(show_servers)
        self.row2_widget.setVisible(bool(row2))

        self._equalize_create_button_widths(show_fin, show_servers)

    def _equalize_create_button_widths(self, show_fin: bool, show_servers: bool) -> None:
        """Фин+серверы: выровнять МИНИМАЛЬНУЮ ширину всех 6 кнопок создания
        (ряд1 «+ПАПКА +СЕРВИС +АККАУНТ» + ряд2 «+КАРТА +КРИПТО +СЕРВЕР»).

        Стретчи _btn_group_stretch выравнивают ширины только пока есть излишек
        места для распределения. На МИНИМАЛЬНОЙ ширине окна излишка нет —
        каждая кнопка садится на свой sizeHint (зависит от длины текста:
        «+ АККАУНТ » шире «+ КАРТА »), и кнопки расходятся. Фиксируем общий
        minimumWidth = максимум sizeHint по всем шести — тогда обе крайности
        (растянуто через стретч / сжато до минимума) дают равные кнопки.

        Пересчитывается при каждом relayout (не только смене темы/шрифта —
        apply_appearance предшествует _relayout_create_buttons в apply_config,
        так что sizeHint уже отражает актуальные метрики).

        В остальных сценариях (только фин / только серверы / оба выкл) —
        снять override (эти раскладки и так устраивают, трогать не просят)."""
        all_buttons = [self.add_folder_btn, self.add_service_btn, self.add_account_btn,
                       *self.fin_buttons_widget.findChildren(QPushButton),
                       *self.srv_buttons_widget.findChildren(QPushButton)]
        if not (show_fin and show_servers):
            for btn in all_buttons:
                btn.setMinimumWidth(0)
            return
        width = max(btn.sizeHint().width() for btn in all_buttons)
        for btn in all_buttons:
            btn.setMinimumWidth(width)

    @staticmethod
    def _btn_group_stretch(w):
        """Стретч виджета ряда кнопок создания: 1 для одиночной QPushButton,
        иначе число вложенных QPushButton (fin_buttons_widget/srv_buttons_widget)."""
        if isinstance(w, QPushButton):
            return 1
        return max(1, len(w.findChildren(QPushButton)))

    def _apply_server_visibility(self):
        """Применить опцию «Показывать серверы»: секция «Привязанные серверы»
        на карточке аккаунта и дерево. При изменении опции — очистить
        открытую карточку сервера/кеш и перестроить дерево. Видимость кнопки
        «+ СЕРВЕР» — в _relayout_create_buttons."""
        show = self.config.get("show_servers", False)
        self.tabs.set_server_section_visible(show)
        prev = getattr(self, "_servers_shown", None)
        self._servers_shown = show
        if prev is None or prev == show:
            return                    # первый вызов (дерево строит __init__) / без изменений
        if not show:
            self._hide_servers_everywhere()
        self._reload_tree()

    def _hide_servers_everywhere(self):
        """Скрытие серверов: закрыть открытую карточку сервера и вычистить
        несохранённые правки серверов из кеша (в UI они больше недоступны).
        Корзина не затрагивается — серверы там видны всегда.

        M-02: перед очисткой состояния — тот же централизованный способ, что
        и при смене сессии БД (_quiesce_card_async/_lock_screen, H65-02):
        инвалидировать поколение карточки (устаревший load/save серверной
        карточки, ещё не завершившийся к моменту выключения тумблера, не
        тронет UI/кеш после проверки gen), снять busy и отменить висящие
        задачи серверной галереи (импорт/предпросмотр) — иначе завершившаяся
        уже ПОСЛЕ выключения загрузка могла бы через orphan-handler создать
        новый серверный черновик или изменить общий UI."""
        self._card_gen += 1
        self._card_busy = False
        try:
            self.server_tabs.f_gallery_widget.cancel_all_tasks()
        except Exception as e:                       # noqa: BLE001 — teardown-хардненинг
            logging.warning("Не удалось отменить задачи серверной галереи: %s", e)
        if self._current_server is not None:
            self._current_server = None
            self.current_server_data = None
            self.is_editing = False
            self._show_placeholder()
        for key in [k for k in self._edit_cache if k[0] == SERVER]:
            self._edit_cache.pop(key, None)
        self._dirty_ids = {k for k in self._dirty_ids if k[0] != SERVER}

    def _apply_fin_visibility(self):
        """Применить опцию «Показывать фин. инструменты»: секция привязанных
        карт на карточке аккаунта и дерево. При изменении опции — очистить
        открытую фин-карточку/кеш и перестроить дерево. Видимость кнопок
        ряда 2 — в _relayout_create_buttons."""
        show = self.config.get("show_fin_instruments", False)
        self.tabs.set_fin_section_visible(show)
        prev = getattr(self, "_fin_shown", None)
        self._fin_shown = show
        if prev is None or prev == show:
            return                    # первый вызов (дерево строит __init__) / без изменений
        if not show:
            self._hide_fin_everywhere()
        self._reload_tree()

    def _hide_fin_everywhere(self):
        """Скрытие фин-инструментов: закрыть открытую фин-карточку и вычистить
        несохранённые правки фин-записей из кеша (в UI они больше недоступны).
        Корзина не затрагивается — фин-записи там видны всегда.

        M-02 (симметрично _hide_servers_everywhere): тот же централизованный
        способ гашения незавершённого async — инвалидировать поколение
        карточки, снять busy, отменить висящие задачи галереи КАЖДОЙ фин-
        карточки (по одной на тип реестра, как в _quiesce_card_async)."""
        self._card_gen += 1
        self._card_busy = False
        try:
            for fin_tabs in self.fin_tabs_by_type.values():
                fin_tabs.f_gallery_widget.cancel_all_tasks()
        except Exception as e:                       # noqa: BLE001 — teardown-хардненинг
            logging.warning("Не удалось отменить задачи фин-галереи: %s", e)
        if self._current_fin is not None:
            self._current_fin = None
            self.current_fin_data = None
            self.is_editing = False
            self._show_placeholder()
        for key in [k for k in self._edit_cache if k[0] in FIN_LEAF_TYPES]:
            self._edit_cache.pop(key, None)
        self._dirty_ids = {k for k in self._dirty_ids if k[0] not in FIN_LEAF_TYPES}

    def export_all(self):
        """Экспорт всей базы — то же, что кнопка «Экспортировать всё» в настройках.

        M-04: диалог открывается сразу, без предварительного чтения БД — снимок
        (отфильтрованный по выбранным разделам) и формирование файла идут в фоне
        уже ПОСЛЕ подтверждения параметров (см. ExportDialog._run_export)."""
        ExportDialog(self.config, self.db, None, None, "Вся база", self,
                     show_fin=self.config.get("show_fin_instruments", False),
                     show_servers=self.config.get("show_servers", False)).exec()

    def open_settings(self):
        dialog = SettingsDialog(self.config, self)
        dialog.set_db_path(self.db.db_path)
        dialog.set_db(self.db)
        # Привилегированные операции (шифрование/бэкап/восстановление) — только
        # монопольно, без конкуренции с фоновым воркером записи (H3-01).
        dialog.set_vault_runner(self.vault.run_exclusive)
        # KDF-тяжёлые операции (Argon2id) — через фоновый поток с модальным
        # progress, чтобы окно не подвисало (H-09).
        dialog.set_vault_runner_heavy(self.vault.run_exclusive_busy)
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
        Данные берутся из текущей БД (в шифр. режиме — из памяти).

        M-04: снимок ветки читается в фоне ПОСЛЕ подтверждения параметров
        диалога, не здесь (см. ExportDialog._run_export)."""
        if node["type"] == ACCOUNT:
            title = self.db.get_account_path(node["id"])
        else:
            title = node["name"]
        ExportDialog(self.config, self.db, node["type"], node["id"], title, self,
                     show_fin=self.config.get("show_fin_instruments", False),
                     show_servers=self.config.get("show_servers", False)).exec()

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

    def _archive_db_file(self, suffix: str = "locked"):
        """Отложить (переименовать) текущий файл БД, не удаляя его.

        suffix: "locked" — недоступный зашифрованный файл (утерян секрет),
        "corrupt" — повреждённый файл SQLite (H-07).

        Возвращает путь к отложенному файлу или None, если файла не было.
        OSError переименования пробрасывается: вызыватель ОБЯЗАН отменить
        замену файла — иначе «сохранённый для диагностики» оригинал был бы
        молча перезаписан."""
        import os
        from datetime import datetime
        if not os.path.exists(self.db.db_path):
            return None
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        archived = f"{self.db.db_path}.{suffix}-{ts}"
        os.replace(self.db.db_path, archived)
        logging.info("Файл БД отложен: %s", archived)
        return archived

    def _create_tables_or_exit(self):
        """create_tables() с понятным отказом, если база создана более новой
        версией программы (схема новее поддерживаемой). «Миграция вниз»
        повредила бы данные, поэтому корректнее завершить работу.

        Повреждение страниц/заголовка SQLite всплывает здесь при первом реальном
        чтении схемы (H-07): fail-closed — соединение закрывается без записи,
        пользователю предлагается восстановление из проверенного бэкапа."""
        import sqlite3
        while True:
            try:
                self.db.create_tables()
                return
            except FutureSchemaError as e:
                logging.error("Несовместимая версия схемы БД: %s", e)
                theme.themed_info(
                    self.config, self, "Несовместимая версия базы", str(e),
                )
            except PreMigrationBackupError as e:
                # Не удалось создать резервную копию перед необратимой правкой
                # схемы (H7-03): база НЕ тронута. Открытие прерываем с понятным
                # сообщением, чтобы пользователь освободил место и повторил.
                logging.error("Отказ открытия: %s", e)
                theme.themed_info(
                    self.config, self, "Не удалось подготовить базу", str(e),
                )
            except sqlite3.DatabaseError as e:
                self.db.close(persist=False)
                if self._handle_db_open_error(e) and self._open_database():
                    continue
            self.vault.shutdown()
            self._instance_lock.release()
            sys.exit(1)

    def _handle_db_open_error(self, err, parent=None) -> bool:
        """Классифицировать ошибку открытия БД: не всякий sqlite3.DatabaseError —
        порча. Временная блокировка внешним процессом (SQLITE_BUSY/LOCKED) не
        должна вести в recovery с заменой здоровой базы бэкапом.

        Возвращает True, если открытие стоит повторить (пользователь просит
        повтор / бэкап восстановлен), False — выход без изменений файла."""
        import sqlite3
        code = getattr(err, "sqlite_errorcode", None)
        primary = code & 0xFF if code is not None else None
        if primary in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            logging.warning("Файл БД временно заблокирован: %s", err)
            return theme.themed_confirm(
                self.config, parent, "База занята",
                "Файл базы временно заблокирован другой программой\n"
                "(SQLite-инструмент, антивирус, резервное копирование).\n\n"
                "Файл не повреждён и не изменён. Повторить попытку открытия?")
        is_corruption = (primary in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)
                         # Нет кода (нестандартная сборка): DatabaseError вне
                         # OperationalError трактуем как порчу структуры.
                         or (code is None
                             and not isinstance(err, sqlite3.OperationalError)))
        if is_corruption:
            return self._offer_corrupt_recovery(err, parent)
        # I/O, права доступа и прочее: файл может быть цел — recovery с заменой
        # не предлагаем, ничего не пишем.
        logging.error("Не удалось открыть файл БД: %s", err)
        theme.themed_info(
            self.config, parent, "Ошибка открытия базы",
            f"Не удалось открыть файл базы:\n{err}\n\n"
            "Файл не изменён. Проверьте диск/права доступа и запустите снова.")
        return False

    def _fail_closed_archive_stranded(self, archived: str, parent=None):
        """Аварийный fail-closed: восстановление сорвалось, И вернуть отложенный
        файл на место не удалось. Продолжать цикл открытия нельзя — connect()
        молча создал бы пустую базу, и пользователь решил бы, что данные
        пропали. Показываем путь к данным; вызыватель обязан завершить запуск."""
        logging.critical("Данные остались в отложенном файле: %s", archived)
        theme.themed_info(
            self.config, parent, "Восстановление прервано",
            "Не удалось восстановить бэкап И вернуть исходный файл на место.\n\n"
            f"Ваши данные сохранены в файле:\n{archived}\n\n"
            "Программа закроется, чтобы не создать пустую базу поверх.\n"
            "Переименуйте этот файл обратно в hranilka.db вручную\n"
            "и запустите программу снова.")

    def _offer_corrupt_recovery(self, err, parent=None) -> bool:
        """Fail-closed обработка повреждённого файла БД (H-07).

        В подозрительный файл ничего не пишется; он откладывается
        (*.corrupt-<ts>) только после явного согласия пользователя на
        восстановление. Возвращает True, если бэкап восстановлен и открытие
        можно повторить, False — пользователь выбрал выход."""
        from hranilka.ui.dialogs.unlock import pick_backup
        from hranilka.ui.theme import ThemedDialog, themed_confirm
        logging.error("Файл БД повреждён: %s", err)

        d = ThemedDialog(self.config, parent)
        d.setWindowTitle("База повреждена")
        d.setMinimumWidth(480)
        lay = d.body
        lay.addWidget(QLabel(
            "Файл базы данных повреждён и не может быть открыт:\n"
            f"{err}\n\n"
            "В повреждённый файл ничего не записано. При восстановлении из\n"
            "бэкапа он будет сохранён рядом (переименован) для диагностики.\n\n"
            "Выберите, как продолжить:"))
        restore_btn = QPushButton("Восстановить из бэкапа…")
        exit_btn = QPushButton("Выход")
        lay.addWidget(restore_btn)
        rr = QHBoxLayout(); rr.addStretch(); rr.addWidget(exit_btn)
        lay.addLayout(rr)

        restored = {"ok": False}

        def do_restore():
            import os
            path = pick_backup(self.config, d)
            if not path:
                return
            # Кандидат проверяется ДО любых изменений текущего файла: невалидный
            # бэкап не должен стоить нам переименованного оригинала.
            if not bk.validate_backup(path):
                theme.themed_info(
                    self.config, d, "Ошибка",
                    "Выбранный файл повреждён или не является базой Хранилки.\n"
                    "Текущие файлы не тронуты.")
                return
            if not themed_confirm(
                    self.config, d, "Восстановление из бэкапа",
                    "Повреждённый файл будет отложен (переименован),\n"
                    "на его место встанет выбранный бэкап. Продолжить?"):
                return
            # Архивирование обязано удаться: обещали сохранить оригинал для
            # диагностики — при сбое восстановление отменяется, файлы не тронуты.
            try:
                archived = self._archive_db_file("corrupt")
            except OSError as e:
                logging.error("Не удалось отложить повреждённый файл: %s", e)
                theme.themed_info(
                    self.config, d, "Ошибка",
                    f"Не удалось отложить повреждённый файл:\n{e}\n"
                    "Восстановление отменено, файлы не тронуты.")
                return
            try:
                # restore_backup повторно проверяет кандидата и имеет свой
                # rollback на время замены (H3-02).
                bk.restore_backup(path, self.db.db_path)
            except Exception as e:                     # noqa: BLE001
                logging.error("Восстановление бэкапа не удалось: %s", e)
                # Вернуть отложенный оригинал: иначе рабочего файла нет и
                # следующий запуск молча создал бы пустую базу.
                if archived is not None:
                    try:
                        os.replace(archived, self.db.db_path)
                    except OSError as e2:
                        logging.error("Не удалось вернуть отложенный файл: %s", e2)
                        # Файл остался под архивным именем — аварийный выход:
                        # restored["ok"]=False, вызыватели завершают запуск.
                        self._fail_closed_archive_stranded(archived, d)
                        d.reject()
                        return
                theme.themed_info(self.config, d, "Ошибка",
                                  f"Не удалось восстановить бэкап:\n{e}")
                return
            restored["ok"] = True
            d.accept()

        restore_btn.clicked.connect(do_restore)
        exit_btn.clicked.connect(d.reject)
        d.exec()
        return restored["ok"]

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
        import sqlite3
        from hranilka.crypto import store as cs
        from hranilka.ui.dialogs import UnlockDialog
        while True:
            if not cs.is_encrypted_file(self.db.db_path):
                try:
                    self.db.connect()
                except sqlite3.DatabaseError as e:
                    # Fail-closed (H-07): ничего не писать. Классификация
                    # отличает порчу (recovery) от временной блокировки
                    # внешним процессом (повтор) и I/O-ошибок (выход).
                    self.db.close(persist=False)
                    if self._handle_db_open_error(e, parent):
                        continue
                    return False
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
                try:
                    self._archive_db_file()
                except OSError as e:
                    # Файл не отложен — новую базу поверх не создаём (обещали
                    # сохранить зашифрованный оригинал). Снова окно разблокировки.
                    theme.themed_info(self.config, parent, "Ошибка",
                                      f"Не удалось отложить файл БД:\n{e}")
                # Файла нет → connect() создаст новую пустую базу.
                continue

            if dlg.recovery_action == "restore" and dlg.restore_path:
                # Кандидат проверяется ДО архивирования: невалидный бэкап не
                # должен стоить нам переименованного оригинала.
                if not bk.validate_backup(dlg.restore_path):
                    theme.themed_info(
                        self.config, parent, "Ошибка",
                        "Выбранный файл повреждён или не является базой "
                        "Хранилки.\nТекущие файлы не тронуты.")
                    continue
                try:
                    archived = self._archive_db_file()
                except OSError as e:
                    theme.themed_info(self.config, parent, "Ошибка",
                                      f"Не удалось отложить файл БД:\n{e}\n"
                                      "Восстановление отменено.")
                    continue
                # Через restore_backup (а не прямой copy2): кандидат проверяется
                # повторно, замена атомарна, есть откат на время подмены (H3-02).
                try:
                    bk.restore_backup(dlg.restore_path, self.db.db_path)
                except Exception as e:
                    # Вернуть отложенный оригинал: иначе рабочего файла нет и
                    # следующая итерация молча создала бы пустую базу.
                    if archived is not None:
                        import os
                        try:
                            os.replace(archived, self.db.db_path)
                        except OSError as e2:
                            logging.error("Не удалось вернуть отложенный файл: %s",
                                          e2)
                            # Аварийный fail-closed: цикл продолжать нельзя.
                            self._fail_closed_archive_stranded(archived, parent)
                            return False
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
            # Durable-копия большой базы (fsync) — в фоне с progress: окно не
            # получает Not Responding. Гейт сохранён (run_exclusive_busy).
            ok, err = self.vault.run_exclusive_busy(
                lambda: bk._copy_durable(Path(db_path), Path(rollback)),
                "Создание страховочной копии текущей базы…")
            if not ok:
                # H65-03: рабочий файл есть, но страховочную копию создать не удалось.
                # Продолжать restore нельзя — при неоткрытии кандидата рабочая БД
                # пропала бы безвозвратно. Отменяем restore и возвращаем прежнюю БД.
                logging.error("Не удалось создать страховочную копию перед restore: %s", err)
                self._discard_file(rollback)
                if self._open_and_validate_after_restore():
                    self._after_db_reopened(None)
                    return False, ("Не удалось создать страховочную копию; "
                                   f"восстановление отменено:\n{err}")
                self.close()
                return False, f"Не удалось создать страховочную копию: {err}"
        # Копирование кандидата, fsync и quick_check — тоже в фоне (гейт тот же).
        ok, err = self.vault.run_exclusive_busy(
            lambda: bk.restore_backup(path, db_path),
            "Восстановление из бэкапа: копирование и проверка…")
        if not ok:
            # restore_backup при сбое откатывает файл к прежнему состоянию —
            # переоткрываем БД как была и сообщаем об ошибке.
            self._discard_file(rollback)
            if not self._open_and_validate_after_restore():
                self.close()
                return False, err
            self._after_db_reopened(None)
            return False, err
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
        self._current_fin = None
        self._current_server = None
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
        # Останавливаем операции карточек/галерей и инвалидируем текущую
        # сессию ДО барьера executor-а. Уже queued run_async не сможет записать
        # данные после очистки; выполняющаяся операция завершится до барьера.
        self._quiesce_card_async()
        self.db.invalidate_async_session()
        if not self.db.wait_executor_idle():
            theme.themed_info(
                self.config, self, "Операции не завершены",
                "Фоновые операции с базой ещё идут. Полная очистка отменена; "
                "подождите несколько секунд и повторите попытку.",
            )
            return
        try:
            self.db.wipe_all_data()
        except Exception as e:                   # noqa: BLE001
            logging.error("Не удалось удалить все данные: %s", e, exc_info=e)
            theme.themed_info(
                self.config, self, "Ошибка удаления",
                "Не удалось удалить все данные. Подробности — в логе программы.",
            )
            return
        # Также удаляем все бэкапы в выбранной папке.
        deleted = 0
        folder = self.config.get("backup_folder", "").strip()
        if folder:
            try:
                deleted = bk.delete_all_backups(folder)
            except Exception as e:
                logging.warning("Не удалось удалить бэкапы: %s", e)
        self._current_account_id = None
        self._current_fin = None
        self._current_server = None
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
