"""Оболочка окна и рантайм-поведение главного окна (Эпик 4.4).

Примесь (mixin) с концернами «окружения» главного окна, не относящимися к
дереву или карточке аккаунта: разворачивание frameless-окна, проброс первого
клика, сохранение/восстановление геометрии, авто-очистка буфера обмена,
авто-блокировка по простою (включая повторную разблокировку зашифрованной БД) и
скриншот-защита. Методы разделяют состояние окна через self — поведение
идентично прежнему, а MainWindow стал тоньше."""
import ctypes
import ctypes.wintypes
import logging
import sys
import time
import uuid

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import (Qt, QEvent, QDateTime, QByteArray, QMimeData, QTimer,
                            QPoint, QSignalBlocker)
from PySide6.QtGui import QKeySequence

from hranilka.ui import sounds, theme
from hranilka.core import shortcuts
from hranilka.data.database import VaultConflictError

_WIN_MIME = 'application/x-qt-windows-mime;value="{}"'
_DWORD_1 = QByteArray(b"\x01\x00\x00\x00")
_DWORD_0 = QByteArray(b"\x00\x00\x00\x00")
# Приватный маркер «этот буфер положила программа» (для картинок, у которых
# текста нет): авто-очистка стирает только содержимое с нашим маркером.
_CLIP_MARK = "application/x-hranilka-clip"

# Размер рамки (px) для hit-test ресайза frameless-окна.
RESIZE_BORDER = 6

# Предел итераций закрытия вложенных модальных окон при авто-блокировке.
_MAX_MODALS = 20


def copy_secret_to_clipboard(text: str) -> None:
    """Единая точка копирования секрета в буфер обмена.

    * Windows: помечает данные форматами, исключающими их из истории буфера
      (Win+V) и облачной синхронизации: ExcludeClipboardContentFromMonitorProcessing=1,
      CanIncludeInClipboardHistory=0, CanUploadToCloudClipboard=0.
    * Запоминает скопированный текст и (пере)взводит таймер авто-очистки главного
      окна (если clipboard_clear_secs > 0) через window._on_field_copied().

    Вызывается откуда угодно (виджеты, диалоги): окно ищется среди
    topLevelWidgets по наличию _on_field_copied; без окна (тесты) копирование
    всё равно выполняется."""
    mime = QMimeData()
    mime.setText(text)
    copy_mime_protected(mime)


def copy_mime_protected(mime: QMimeData) -> None:
    """Положить в буфер готовый QMimeData (текст, картинка…) с защитой: форматы
    исключения из истории/облака Windows, приватный маркер для авто-очистки и
    взвод таймера очистки главного окна. Публичная точка для виджетов, которым
    нужен не только текст (галерея)."""
    if sys.platform == "win32":
        mime.setData(_WIN_MIME.format("ExcludeClipboardContentFromMonitorProcessing"), _DWORD_1)
        mime.setData(_WIN_MIME.format("CanIncludeInClipboardHistory"), _DWORD_0)
        mime.setData(_WIN_MIME.format("CanUploadToCloudClipboard"), _DWORD_0)
    mime.setData(_CLIP_MARK, uuid.uuid4().hex.encode("ascii"))
    QApplication.clipboard().setMimeData(mime)
    for w in QApplication.topLevelWidgets():
        handler = getattr(w, "_on_field_copied", None)
        if callable(handler) and hasattr(w, "_clip_timer"):
            handler()


def hit_test(x: int, y: int, w: int, h: int, border: int = RESIZE_BORDER):
    """Чистая функция: зона рамки по координатам внутри окна w x h.
    Возвращает строку из {l,r,t,b,tl,tr,bl,br} или None."""
    left, right = x < border, x >= w - border
    top, bottom = y < border, y >= h - border
    d = ("t" if top else "b" if bottom else "") + ("l" if left else "r" if right else "")
    return d or None


_HT = {"l": 10, "r": 11, "t": 12, "tl": 13, "tr": 14, "b": 15, "bl": 16, "br": 17}


class WindowChromeMixin:
    """Frameless-окно, геометрия, буфер обмена, idle-блокировка, скриншот-защита."""

    _locking = False     # идёт авто-блокировка (в MainWindow задаётся в __init__)
    _closing = False     # closeEvent завершает работу с БД

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
            if msg.message == 0x0084 and not getattr(self, "_is_max", False):
                # WM_NCHITTEST: ресайз за все грани/углы (в т.ч. верхние).
                lp = int(msg.lParam)
                gx = ctypes.c_short(lp & 0xFFFF).value
                gy = ctypes.c_short((lp >> 16) & 0xFFFF).value
                dpr = self.devicePixelRatioF() or 1.0
                pos = self.mapFromGlobal(QPoint(int(gx / dpr), int(gy / dpr)))
                d = hit_test(pos.x(), pos.y(), self.width(), self.height())
                if d:
                    return True, _HT[d]
            elif msg.message == 0x02B1 and int(msg.wParam) == 7:  # WTS_SESSION_LOCK
                self._on_session_event()
            elif msg.message == 0x0218 and int(msg.wParam) == 4:  # PBT_APMSUSPEND
                self._on_session_event()
        return super().nativeEvent(event_type, message)

    def _on_session_event(self):
        """Блокировка сессии Windows / уход в сон → авто-блокировка (S10).
        Выключено по умолчанию (config lock_on_session_lock)."""
        try:
            enabled = bool(self.config.get("lock_on_session_lock", False))
        except Exception:                                   # noqa: BLE001
            enabled = False
        if enabled and not self._unlocking:
            QTimer.singleShot(0, self._lock_screen_on_session)

    def _lock_screen_on_session(self):
        if self._unlocking or self._locking or self._closing:
            return
        if (getattr(self.vault, "_vault_locked", False)
                or getattr(self.vault, "_quiescing", False)):
            # Событие сессии одноразовое: идёт операция с базой — повторяем,
            # иначе при выключенном idle-таймере хранилище осталось бы открытым.
            QTimer.singleShot(1000, self._lock_screen_on_session)
            return
        if not self.db.encrypted and not self._any_card_open():
            return
        # Модальные окна (настройки и т.п.) закрывает сам _lock_screen: общий
        # путь с idle-блокировкой.
        self._lock_screen()

    def register_session_notifications(self):
        """Подписаться на WM_WTSSESSION_CHANGE (вызывать после show())."""
        if sys.platform != "win32":
            return False
        try:
            hwnd = int(self.winId())
            return bool(ctypes.windll.wtsapi32.WTSRegisterSessionNotification(hwnd, 0))
        except Exception as e:                              # noqa: BLE001
            logging.warning("WTSRegisterSessionNotification: %s", e)
            return False

    def _restore_geometry(self):
        """Восстановить размер/положение окна (только при старте)."""
        if self.config.get("remember_geometry"):
            geo_hex = self.config.get("_window_geometry", "")
            if geo_hex:
                self.restoreGeometry(QByteArray.fromHex(bytes(geo_hex, "ascii")))
                self._ensure_on_screen()

    def _ensure_on_screen(self):
        """Если восстановленная геометрия не попадает ни на один доступный экран
        (монитор отключили / сменился DPI) — вернуть окно в центр основного
        экрана, при необходимости ужав до его размеров (L-13)."""
        frame = self.frameGeometry()
        if any(s.availableGeometry().intersects(frame)
               for s in QApplication.screens()):
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        self.resize(min(self.width(), avail.width()),
                    min(self.height(), avail.height()))
        geo = self.frameGeometry()
        geo.moveCenter(avail.center())
        self.move(geo.topLeft())

    # ─── Буфер обмена ────────────────────────────────────────────────────────

    def _on_field_copied(self):
        secs = self.config.get("clipboard_clear_secs", 0)
        # Запоминаем, что именно скопировали: очищаем буфер только если там всё
        # ещё наш текст (B8) — чужое содержимое не стираем.
        cb = QApplication.clipboard()
        self._clip_copied_text = cb.text()
        md = cb.mimeData()
        self._clip_mark = (bytes(md.data(_CLIP_MARK))
                           if md is not None and md.hasFormat(_CLIP_MARK) else b"")
        sounds.play(self.config, "copy")
        if secs > 0:
            self.statusBar().showMessage(
                f"СКОПИРОВАНО!  (буфер очистится через {secs} сек.)", 3000
            )
            self._clip_timer.start(secs * 1000)
        else:
            self.statusBar().showMessage("СКОПИРОВАНО!", 2000)

    def _clipboard_is_ours(self) -> bool:
        """В буфере всё ещё то, что скопировала программа: тот же приватный
        маркер (картинки, у которых текста нет) либо тот же непустой текст.
        Пустой текст сам по себе ничего не доказывает — чужую картинку/файлы
        по нему не стираем."""
        cb = QApplication.clipboard()
        md = cb.mimeData()
        mark = getattr(self, "_clip_mark", b"")
        if mark and md is not None and md.hasFormat(_CLIP_MARK) \
                and bytes(md.data(_CLIP_MARK)) == mark:
            return True
        copied = getattr(self, "_clip_copied_text", None)
        return bool(copied) and cb.text() == copied

    def _clear_clipboard(self):
        cb = QApplication.clipboard()
        tracked = getattr(self, "_clip_copied_text", None) is not None
        ours = self._clipboard_is_ours()
        self._clip_copied_text = None
        self._clip_mark = b""
        if tracked and not ours:
            self.statusBar().showMessage(
                "Буфер обмена изменён - не очищен.", 2500)
            return
        cb.clear()
        self.statusBar().showMessage("Буфер обмена очищен.", 2000)

    def _finish_ui_state(self):
        """Идемпотентный выходной блок UI: геометрия окна и (если включено)
        очистка буфера с секретом. Общий для closeEvent (_finish_close) и
        _exit_app (отказ в окне пароля): иначе секрет оставался бы в буфере."""
        if self.config.get("remember_geometry"):
            try:
                self.config.set("_window_geometry",
                                bytes(self.saveGeometry().toHex()).decode("ascii"))
                self.config.save()
            except Exception as e:                          # noqa: BLE001
                logging.warning("Не удалось сохранить геометрию окна: %s", e)
        if self.config.get("clipboard_clear_on_exit", False):
            try:
                # Чужое содержимое не стираем — только наш секрет/картинку.
                if self._clipboard_is_ours():
                    QApplication.clipboard().clear()
                    self._clip_copied_text = None
                    self._clip_mark = b""
            except Exception as e:                          # noqa: BLE001
                logging.warning("Не удалось очистить буфер при выходе: %s", e)

    # ─── Idle-блокировка ─────────────────────────────────────────────────────

    _ACTIVITY_EVENTS = (QEvent.Type.MouseMove, QEvent.Type.KeyPress,
                        QEvent.Type.MouseButtonPress, QEvent.Type.Wheel)

    def eventFilter(self, obj, ev):
        # Активность в любом окне программы (главное, настройки, диалоги)
        # сбрасывает счётчик простоя. Пока идёт повторная разблокировка —
        # активность не учитываем (иначе ввод пароля «продлевал» бы сессию).
        if ev.type() in self._ACTIVITY_EVENTS and not self._unlocking:
            # Троттлинг (P6): QDateTime создаём не чаще раза в секунду.
            now = time.monotonic()
            if now - getattr(self, "_act_mono", 0.0) >= 1.0:
                self._act_mono = now
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

    def _any_card_open(self):
        """True, если в правой панели открыта любая карточка — аккаунт, фин.
        запись или VPS-сервер. Общий предикат для idle-lock (H-01) и
        контекстных хоткеев (M-01): раньше проверялся только
        `_current_account_id`, из-за чего открытая фин-/VPS-карточка не
        считалась «картой на экране»."""
        return (self._current_account_id is not None
                or self._current_fin is not None
                or self._current_server is not None)

    def note_activity(self):
        """Сбросить счётчик простоя (idle-lock). Вызывает VaultController после
        привилегированных операций: их длительность (KDF на большой базе) не
        должна засчитываться как бездействие пользователя."""
        self._act_mono = time.monotonic()
        self._last_activity = QDateTime.currentDateTime()

    def _check_idle(self):
        mins = self.config.get("idle_lock_mins", 0)
        # _locking/_closing: блокировка уже идёт (вложенные event-loop'ы её
        # диалогов крутят таймеры) либо closeEvent завершает работу с БД.
        if mins <= 0 or self._unlocking or self._locking or self._closing:
            return
        # Идёт привилегированная операция над файлом (run_exclusive_busy крутит
        # модальный event-loop, таймеры живы): блокировать БД сейчас нельзя.
        if getattr(self.vault, "_vault_locked", False):
            return
        # В обычном режиме прячем только открытую карточку (аккаунт, фин.
        # запись или сервер — H-01); в зашифрованном — блокируем всю базу
        # (снимаем ключ), даже если карточка не открыта.
        if not self.db.encrypted and not self._any_card_open():
            return
        elapsed_secs = self._last_activity.secsTo(QDateTime.currentDateTime())
        if elapsed_secs >= mins * 60:
            self._lock_screen()

    def _close_modals_for_lock(self) -> bool:
        """Закрыть открытые модальные окна, чтобы блокировка прошла даже с
        открытыми настройками. Возвращает False, если блокировку надо отложить:
        открыто окно, которое закрывать нельзя (идёт операция, одноразовый
        recovery-код, ввод пароля), либо окно не закрылось.

        Диалоги закрываются через force_close() (без вложенных подтверждений:
        reject() у настроек/recovery их открывает и сам заблокировал бы блокировку)."""
        from hranilka.ui.dialogs.recovery import RecoveryCodeDialog
        from hranilka.ui.dialogs.unlock import UnlockDialog
        from hranilka.ui.vault_controller import _BusyDialog
        protected = (_BusyDialog, RecoveryCodeDialog, UnlockDialog)
        for _ in range(_MAX_MODALS):
            modal = QApplication.activeModalWidget()
            if modal is None or modal is self:
                return True
            if isinstance(modal, protected):
                return False
            force = getattr(modal, "force_close", None)
            if callable(force):
                force()
            else:
                modal.reject()
            if QApplication.activeModalWidget() is modal:
                return False                     # окно не закрылось
        return QApplication.activeModalWidget() in (None, self)

    def _lock_screen(self):
        if self._locking or self._closing:
            return
        # H6-05: откладываем авто-блокировку при несохранённых правках ТОЛЬКО в
        # зашифрованном режиме — там lock обнуляет ключ, и плейн-черновики из
        # памяти были бы потеряны. Таймер не останавливаем: на следующем интервале
        # проверка повторится, и как только правок не останется — блокировка пройдёт.
        if self.db.encrypted and (self.is_editing or self._dirty_ids
                                  or self._edit_cache):
            self.statusBar().showMessage(
                "Авто-блокировка отложена: есть несохранённые изменения.", 4000)
            return
        # Таймер простоя НЕ останавливаем: от повторного входа защищают _locking и
        # _unlocking, а при любом исходе (отказ, ранний выход) проверка продолжится.
        # Привилегированная операция вот-вот стартует (_quiesce в VaultController):
        # блокировать нельзя, иначе idle-lock закроет её диалог настроек.
        if getattr(self.vault, "_quiescing", False):
            return
        self._locking = True
        try:
            # Фон должен успокоиться ДО закрытия модальных окон: при таймауте
            # блокировка всё равно отменится, а ввод в диалогах пропал бы зря.
            if self.db.encrypted and not (self.vault.wait_idle()
                                          and self.db.wait_executor_idle()):
                self.statusBar().showMessage(
                    "Авто-блокировка отложена: фоновое сохранение ещё идёт.", 4000)
                self.note_activity()
                return
            if not self._close_modals_for_lock():
                self.statusBar().showMessage(
                    "Авто-блокировка отложена: открыто окно, которое нельзя "
                    "закрыть автоматически.", 4000)
                return
            if self.db.encrypted:
                self._lock_vault()
            else:
                self._hide_card_plaintext()
        finally:
            self._locking = False

    def _hide_card_plaintext(self):
        """Обычный (plaintext) режим: «блокировка» лишь скрывает карточку с
        экрана, ключ не теряется — несохранённые правки безопасно стэшим в кеш и
        восстанавливаем при возврате (H5-03). Стэшим черновик ТЕКУЩЕГО открытого
        типа — аккаунт, фин-запись или сервер открыты не одновременно (одна
        правая панель), маршрутизация как в _save_unsaved_before_exit. Дерево и
        поиск остаются: в этом режиме скрывается только карточка."""
        # Гасим незавершённый async карточки/галереи (H65-02).
        self._quiesce_card_async()
        stashed = False
        if self.is_editing and self._current_fin is not None:
            self._stash_current_fin_edits()
            stashed = True
        elif self.is_editing and self._current_server is not None:
            self._stash_current_server_edits()
            stashed = True
        elif self.is_editing and self._current_account_id is not None:
            self._stash_current_edits(self._current_account_id)
            stashed = True
        if stashed:
            self._refresh_dirty_markers()
        self._reset_card_state()
        self._update_status_info()
        self.statusBar().showMessage(
            "Данные скрыты (авто-блокировка по простою).", 4000)

    def _reset_card_state(self):
        """Спрятать карточку и забыть её данные (H-01): сбрасываем ВСЕ три
        current-состояния и их data-объекты — симметрично
        _hide_fin_everywhere/_hide_servers_everywhere. Иначе секреты открытой
        карточки оставались бы в памяти, а сама она считалась «открытой»
        (_any_card_open) сразу после блокировки. Скрытые вкладки тоже очищаем:
        секреты лежат в их QLineEdit и пока вкладка не показана."""
        self._show_placeholder()
        self._current_account_id = None
        self._current_fin = None
        self.current_fin_data = None
        self._current_server = None
        self.current_server_data = None
        self.current_account_data = None
        self.is_editing = False
        self._clear_card_widgets()

    def _clear_card_widgets(self):
        """Обнулить значения всех полей карточек (аккаунт, фин, сервер)."""
        tab_sets = [getattr(self, "tabs", None), getattr(self, "server_tabs", None)]
        tab_sets.extend(getattr(self, "fin_tabs_by_type", {}).values())
        for tabs in tab_sets:
            if tabs is None:
                continue
            for w in tabs.findChildren(QWidget):
                try:
                    if hasattr(w, "set_data"):
                        w.set_data([])
                    elif hasattr(w, "set_items"):
                        w.set_items([])
                    elif hasattr(w, "set_text"):
                        w.set_text("")
                    elif hasattr(w, "set_date"):
                        w.set_date(None)
                except RuntimeError:
                    pass             # C++-объект уже удалён (deleteLater)
                except Exception as e:                      # noqa: BLE001
                    logging.warning("Очистка поля карточки при блокировке: %s", e)

    def _clear_tree_and_search(self):
        """Зашифрованная база заблокирована: дерево (названия аккаунтов) и текст
        поиска не должны оставаться на экране. После разблокировки дерево
        перестраивается из БД (_reload_tree)."""
        # Снимок для восстановления после разблокировки: только ключи
        # (type, id) раскрытых узлов и выбранного, плюс текст поиска. Названий
        # записей в нём нет; потребляется (и обнуляется) в _unlock_vault.
        pre = getattr(self, "_pre_search_expanded", None)
        sel = self._node(self.tree.currentItem())
        self._lock_ui_snapshot = {
            "expanded": set(pre) if pre is not None else self._expanded_keys(),
            "selected": (sel["type"], sel["id"]) if sel else None,
            "search": self.search_box.text(),
        }
        timer = getattr(self, "_search_timer", None)
        if timer is not None:
            timer.stop()
        blocker = QSignalBlocker(self.tree)
        try:
            self.tree.clear()
        finally:
            blocker.unblock()
        self.current_tree_item = None
        box_blocker = QSignalBlocker(self.search_box)
        try:
            self.search_box.clear()
        finally:
            box_blocker.unblock()
        self.search_text = ""
        self._pre_search_expanded = None
        self.status_info.setText("")

    def _lock_vault(self):
        """Зашифрованный режим: снять ключ и потребовать повторный ввод пароля.
        Поддерживает тот же путь восстановления («забыли пароль»), что и старт.

        Интерфейс (карточка, дерево, поиск) очищается только ПОСЛЕ успешного
        db.lock(): при отказе блокировки данные остаются доступными, а idle-таймер
        (он не останавливался) повторит попытку через полный интервал простоя."""
        if not self._persist_and_lock_db():
            # Повтор через полный интервал простоя, а не диалогом на каждый тик.
            self.note_activity()
            return
        sounds.play(self.config, "lock")
        self.status_panels.set_locked(True)
        # Сюда попадаем только без несохранённых правок (см. _lock_screen, H6-05),
        # поэтому кеш уже пуст — отдельная очистка не нужна.
        self._reset_card_state()
        self._clear_tree_and_search()
        self._unlock_vault()

    def _persist_and_lock_db(self) -> bool:
        """Дождаться фона, сохранить и закрыть БД из памяти (ключ обнуляется).
        False — блокировка отменена, БД и ключ целы."""
        # Если сохранение не удастся, lock() пробросит исключение и НЕ обнулит
        # ключ/БД — прерываем блокировку, чтобы не потерять несохранённые данные.
        if not self.vault.wait_idle():
            theme.themed_info(
                self.config, self, "Блокировка отменена",
                "Фоновое сохранение базы не завершилось вовремя.\n"
                "Блокировка отменена, данные остались доступны.",
            )
            return False
        # Барьер run_async: in-flight мутаторы (сохранение карточки) должны
        # дойти до lock(). Иначе мутатор, поставленный до блокировки, после неё
        # получил бы StaleSessionError и правка тихо потерялась бы. Сам lock()
        # сериализован с ними RLock'ом; барьер лишь не даёт им протухнуть.
        if not self.db.wait_executor_idle():
            theme.themed_info(
                self.config, self, "Блокировка отменена",
                "Фоновые операции с базой не завершились вовремя.\n"
                "Блокировка отменена, данные остались доступны.",
            )
            return False
        # Висящие загрузки/сохранения/предпросмотр не должны примениться к UI уже
        # заблокированной сессии (H65-02) — гасятся сразу после успешного lock().
        # Карточку гасим только когда блокировка состоялась (иначе при отказе
        # lock() карточка осталась бы открытой с отменёнными задачами галереи).
        # Всё в GUI-потоке синхронно: между lock() и гашением async не вклинится.
        try:
            self.db.lock()
            self._quiesce_card_async()
            return True
        except VaultConflictError:
            if not theme.themed_confirm(
                self.config, self, "Файл изменён извне",
                "Файл базы был изменён другой программой с момента открытия.\n"
                "Перезаписать его своими данными?",
            ):
                return False  # блокировку отменяем, данные остаются доступны
            try:
                self.db.lock(force=True)
                self._quiesce_card_async()
                return True
            except Exception as e:
                logging.warning("Не удалось сохранить БД перед блокировкой: %s", e)
                theme.themed_info(
                    self.config, self, "Ошибка сохранения",
                    f"Не удалось сохранить базу на диск:\n{e}\n\n"
                    "Блокировка отменена, данные остались доступны.",
                )
                return False
        except Exception as e:
            logging.warning("Не удалось сохранить БД перед блокировкой: %s", e)
            theme.themed_info(
                self.config, self, "Ошибка сохранения",
                f"Не удалось сохранить базу на диск:\n{e}\n\n"
                "Блокировка отменена, данные остались доступны. Освободите "
                "место/проверьте доступ к файлу и повторите.",
            )
            return False

    def _unlock_vault(self):
        """Повторный ввод пароля после блокировки. Любой отказ завершает
        приложение штатно (_exit_app): слот таймера не должен ни бросать
        SystemExit, ни оставлять окно с закрытой БД."""
        self._unlocking = True
        try:
            ok = self._open_database(self)
        except Exception as e:                              # noqa: BLE001
            logging.error("Повторное открытие БД не удалось: %s", e, exc_info=e)
            self._exit_app(1)
            return
        finally:
            self._unlocking = False
        if not ok:
            self._exit_app(0)          # пользователь выбрал «Выход»
            return
        self.status_panels.set_locked(False)
        # Валидация/миграция схемы после разблокировки (единый путь с рестартом,
        # H65-05): _open_database открывает только соединение и схему не трогает.
        try:
            self._create_tables_or_exit()
        except SystemExit as e:        # StartupAborted: ресурсы уже освобождены
            code = e.code
            self._exit_app(code if isinstance(code, int) else 1)
            return
        except Exception as e:                              # noqa: BLE001
            logging.error("Проверка схемы после разблокировки: %s", e, exc_info=e)
            self._exit_app(1)
            return
        self._reload_tree()
        self._restore_ui_after_unlock()
        self._last_activity = QDateTime.currentDateTime()
        self.statusBar().showMessage("База разблокирована.", 3000)
        sounds.play(self.config, "unlock")

    def _restore_ui_after_unlock(self):
        """Вернуть раскрытие, выбор и поиск, снятые в _clear_tree_and_search.
        Снимок одноразовый: обнуляется сразу. Выбор ставится с сигналами, чтобы
        карточка открылась: подсветка без карточки не реагировала бы на клик
        (currentItemChanged не срабатывает для уже текущего элемента)."""
        snap = getattr(self, "_lock_ui_snapshot", None)
        self._lock_ui_snapshot = None
        if not snap:
            return
        text = snap.get("search", "")
        if text:
            box_blocker = QSignalBlocker(self.search_box)
            try:
                self.search_box.setText(text)
            finally:
                box_blocker.unblock()
            self.search_text = text.strip().lower()
        if self._filter_active():
            # Раскрытие «до поиска» вернётся при его сбросе (_refresh_filter).
            self._pre_search_expanded = set(snap["expanded"])
            self._apply_filter()
            self._update_dnd_mode()
        else:
            self._restore_expanded(snap["expanded"])
        key = snap.get("selected")
        if key is not None:
            for it in self._iter_items():
                node = self._node(it)
                if node and (node["type"], node["id"]) == key:
                    self.tree.setCurrentItem(it)
                    break
        self._update_tree_toggle_btn()

    def _exit_app(self, code: int = 0):
        """Штатно завершить приложение из слота (таймер/событие), где closeEvent
        мог бы быть отклонён, а SystemExit — потерян. Останавливаем таймеры,
        поток записи, executor БД, снимаем instance-lock (все шаги идемпотентны) и
        просим QApplication выйти."""
        self._closing = True
        self._idle_timer.stop()
        self._lock_ui_snapshot = None
        # Геометрия и буфер — до hide(); БД уже заблокирована и сброшена, бэкап
        # на этом пути не нужен.
        self._finish_ui_state()
        self.hide()
        for step in (self.vault.shutdown, self.db.shutdown_executor,
                     self._instance_lock.release):
            try:
                step()
            except Exception as e:                          # noqa: BLE001
                logging.warning("Завершение после отказа разблокировки: %s", e)
        QApplication.exit(code)

    # ─── Скриншот-защита ─────────────────────────────────────────────────────

    def _apply_screenshot_protect(self, enabled: bool):
        """Скриншот-защита главного окна. Результат WinAPI проверяется (M-01):
        при включении, которое НЕ применилось, пользователь видит честный
        статус, а не молчаливое ложное чувство защиты."""
        ok = theme.apply_screenshot_protect(self, enabled)
        if enabled and not ok:
            self.statusBar().showMessage(
                "ВНИМАНИЕ: защита от скриншотов НЕ применилась (см. лог).", 8000)
