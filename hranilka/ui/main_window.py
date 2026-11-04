"""Главное окно программы (MainWindow): сборка UI из mixin-ов (дерево,
карточка, хром окна, хоткеи), открытие/восстановление БД, тема. Точка входа
и qasync-цикл — в hranilka/app.py (этап 6 реструктуризации)."""
import atexit
import logging
from typing import Optional
from collections import Counter
from PySide6.QtWidgets import (QApplication, QMainWindow, QSplitter, QLabel,
                               QVBoxLayout, QWidget, QHBoxLayout,
                               QPushButton, QLineEdit, QStackedWidget,
                               QComboBox, QAbstractItemView)
from PySide6.QtCore import Qt, QTimer, QDateTime
from hranilka.services import backup as bk

from hranilka.core.config import Config
from hranilka.data.database import Database, VaultConflictError
from hranilka.ui.status_panels import StatusPanels
from hranilka.ui.vault_controller import VaultController
from hranilka.ui.chrome import WindowChromeMixin
from hranilka.ui.shortcuts_mixin import ShortcutsMixin
from hranilka.ui.account_card import AccountCardMixin
from hranilka.ui.fin_card import FinCardMixin
from hranilka.ui.server_card import ServerCardMixin
from hranilka.ui.tree import AccountTree, TreeMixin
from hranilka.core import instance_lock
from hranilka.core.nodetypes import ACCOUNT, FIN_LEAF_TYPES, SERVER
from hranilka.core.fin_types import FIN_TYPES
from hranilka.core.paths import BASE_DIR
from hranilka.ui.dialogs import SettingsDialog, RecycleBinDialog, ExportDialog
from hranilka.ui.tabs import AccountTabs
from hranilka.ui.fin_tabs import FinItemTabs
from hranilka.ui.server_tabs import ServerTabs
from hranilka.ui.titlebar import TitleBar, ResizableContainer
from hranilka.ui import theme
from hranilka.ui.appearance import AppearanceMixin
from hranilka.ui.db_lifecycle import DbLifecycleMixin, StartupAborted
from hranilka.ui.sidebar import SidebarMixin
from hranilka.ui.update_controller import UpdateController


class MainWindow(WindowChromeMixin, ShortcutsMixin, ServerCardMixin, FinCardMixin,
                 AccountCardMixin, TreeMixin, SidebarMixin, AppearanceMixin,
                 DbLifecycleMixin, QMainWindow):
    last_exit_code = 0

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
            logging.warning("Запуск отменён: файл БД занят другим экземпляром")
            raise StartupAborted(0, "vault locked by another instance")
        # Снятие блокировки при любом завершении процесса (страховка на случай
        # путей выхода помимо closeEvent).
        atexit.register(self._instance_lock.release)

        # Вся работа с файлом-БД (фоновая запись, отложенный flush, монопольный
        # гейт) — в VaultController. Любой отказ запуска ниже освобождает
        # блокировку и поток записи, затем пробрасывает StartupAborted.
        self.vault = VaultController(self.db, self, self.config)
        try:
            if not self._open_database():
                # Пользователь выбрал «Выход» в окне разблокировки.
                raise StartupAborted(0, "unlock cancelled")
            self._create_tables_or_exit()
        except BaseException:
            self.vault.shutdown()
            self._instance_lock.release()
            raise

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
        self._updates = UpdateController(self)
        QTimer.singleShot(0, self._updates.on_startup)

    @classmethod
    def create(cls) -> "Optional[MainWindow]":
        """Фабрика без исключений для точки входа: None, если запуск прерван
        (занята БД, «Выход» в окне разблокировки, несовместимая схема). Код
        возврата — в `cls.last_exit_code`."""
        cls.last_exit_code = 0
        try:
            return cls()
        except StartupAborted as e:
            logging.info("Запуск прерван: %s (код %s)", e.reason, e.code)
            cls.last_exit_code = e.code if isinstance(e.code, int) else 0
            return None

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
            if not self._validate_card_name(
                    self.fin_tabs, "финансовой записи"):
                return False
            self._stash_current_fin_edits()
        elif self.is_editing and self._current_server is not None:
            if not self._validate_card_name(self.server_tabs, "сервера"):
                return False
            self._stash_current_server_edits()
        elif self.is_editing and self._current_account_id is not None:
            if not self._validate_card_name(self.tabs, "аккаунта"):
                return False
            self._stash_current_edits(self._current_account_id)
        show_fin = self.config.get("show_fin_instruments", False)
        show_servers = self.config.get("show_servers", False)

        # Сначала проверяем весь кеш, чтобы «Сохранить и выйти» не успело
        # частично записать другие черновики перед карточкой без названия.
        for (node_type, _rec_id), cached in self._edit_cache.items():
            storage = cached.get("storage") or {}
            name = (storage.get("fields", {}).get("account_name")
                    if node_type == ACCOUNT else storage.get("name"))
            if not str(name or "").strip():
                theme.themed_info(
                    self.config, self, "Не заполнено название",
                    "У одной из карточек в черновиках не заполнено "
                    "обязательное название. Выход отменён.")
                return False

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
                # Как _clear_clipboard (chrome): чужое содержимое не стираем —
                # только если в буфере всё ещё скопированный нами секрет.
                copied = getattr(self, "_clip_copied_text", None)
                cb = QApplication.clipboard()
                if copied is not None and cb.text() == copied:
                    cb.clear()
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
        self.search_box.textChanged.connect(self.on_search_text_edited)  # debounce → on_search_changed
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

        # Общее сворачивание/разворачивание дерева. Размер совпадает с кнопкой
        # направления сортировки; иконка — дерево, без ещё одной двусмысленной
        # стрелки рядом с сортировкой.
        self.tree_toggle_btn = QPushButton()
        self.tree_toggle_btn.setFixedWidth(36)
        self.tree_toggle_btn.clicked.connect(self.on_tree_toggle_all)
        sort_layout.addWidget(self.tree_toggle_btn)
        left_layout.addLayout(sort_layout)

        self.tree = AccountTree()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(25)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        # AccountTree рисует собственную полноширинную линию вставки: штатный
        # индикатор Qt использует узкие зоны Above/Below и мешает точному drop.
        self.tree.setDropIndicatorShown(False)
        self.tree.setDefaultDropAction(Qt.MoveAction)  # InternalMove = перемещение
        self.tree.setAutoScroll(True)
        self.tree.setAutoScrollMargin(48)
        self._update_dnd_mode()
        self.tree.order_changed.connect(self.on_tree_order_changed)
        self.tree.reparent_requested.connect(self.on_tree_reparent)
        self.tree.drop_hint_changed.connect(self.on_tree_drop_hint_changed)
        self.tree.drop_rejected.connect(
            lambda: self.statusBar().showMessage(
                "Перетаскивание меняет порядок только внутри одной группы. "
                "Для переноса используйте ПКМ → «Переместить…»", 4000)
        )
        self.tree.currentItemChanged.connect(self.on_item_selected)
        self.tree.itemExpanded.connect(self._update_tree_toggle_btn)
        self.tree.itemCollapsed.connect(self._update_tree_toggle_btn)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_tree_context_menu)
        self._update_tree_toggle_btn()
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
        self.status_info.setProperty("statusPanel", "true")
        self.statusBar().addPermanentWidget(self.status_info)
        # Утопленные панели справа (7.4): хранилище, записи, CAPS, часы.
        self.status_panels = StatusPanels(
            self.statusBar(), self.tree, lambda: bool(self.db.encrypted), self)

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

        # Новая настройка внешнего вида применяется к уже открытой карточке
        # сразу, без её закрытия/повторного выбора в дереве.
        self._apply_empty_card_visibility()

        # Раскладка рядов кнопок создания зависит от обоих тумблеров сразу —
        # пересчитывается один раз после того, как оба флага применены.
        self._relayout_create_buttons()

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
        dialog.appearance_changed.connect(self.request_appearance)  # предпросмотр (debounce)
        dialog.settings_applied.connect(self.apply_config)        # полное применение
        dialog.settings_applied.connect(self._rebind_shortcuts)   # пере-привязка хоткеев
        # Восстановление из бэкапа: всю последовательность (закрыть БД → заменить
        # файл → переоткрыть → обновить UI) выполняет MainWindow, диалог лишь
        # запрашивает её и показывает результат (см. _restore_from_backup).
        dialog.set_restore_handler(self._restore_from_backup)
        dialog.set_update_checker(self._updates.check_now)
        dialog.exec()
        self.flush_appearance()             # отложенный предпросмотр — до конца
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

    def open_selected_export(self, nodes):
        """Открыть обычный диалог экспорта для нескольких узлов дерева."""
        selected_nodes = tuple((node["type"], node["id"]) for node in nodes)
        if not selected_nodes:
            return
        title = f"Выбранные элементы ({len(selected_nodes)})"
        ExportDialog(
            self.config, self.db, None, None, title, self,
            show_fin=self.config.get("show_fin_instruments", False),
            show_servers=self.config.get("show_servers", False),
            selected_nodes=selected_nodes).exec()

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
