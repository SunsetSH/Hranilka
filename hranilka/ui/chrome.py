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

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QEvent, QDateTime, QByteArray, QMimeData, QTimer, QPoint
from PySide6.QtGui import QKeySequence

from hranilka.ui import sounds, theme
from hranilka.core import shortcuts
from hranilka.data.database import VaultConflictError

_WIN_MIME = 'application/x-qt-windows-mime;value="{}"'
_DWORD_1 = QByteArray(b"\x01\x00\x00\x00")
_DWORD_0 = QByteArray(b"\x00\x00\x00\x00")

# Размер рамки (px) для hit-test ресайза frameless-окна.
RESIZE_BORDER = 6


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
    if sys.platform == "win32":
        mime.setData(_WIN_MIME.format("ExcludeClipboardContentFromMonitorProcessing"), _DWORD_1)
        mime.setData(_WIN_MIME.format("CanIncludeInClipboardHistory"), _DWORD_0)
        mime.setData(_WIN_MIME.format("CanUploadToCloudClipboard"), _DWORD_0)
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
        if getattr(self.vault, "_vault_locked", False) or self._unlocking:
            return
        if not self.db.encrypted and not self._any_card_open():
            return
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
        self._clip_copied_text = QApplication.clipboard().text()
        sounds.play(self.config, "copy")
        if secs > 0:
            self.statusBar().showMessage(
                f"СКОПИРОВАНО!  (буфер очистится через {secs} сек.)", 3000
            )
            self._clip_timer.start(secs * 1000)
        else:
            self.statusBar().showMessage("СКОПИРОВАНО!", 2000)

    def _clear_clipboard(self):
        cb = QApplication.clipboard()
        copied = getattr(self, "_clip_copied_text", None)
        self._clip_copied_text = None
        if copied is not None and cb.text() != copied:
            self.statusBar().showMessage(
                "Буфер обмена изменён - не очищен.", 2500)
            return
        cb.clear()
        self.statusBar().showMessage("Буфер обмена очищен.", 2000)

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
        if mins <= 0 or self._unlocking:
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
            # Если открыто модальное окно (например, настройки) — закрываем его,
            # чтобы блокировка сработала даже с открытыми настройками.
            modal = QApplication.activeModalWidget()
            if modal is not None and modal is not self:
                modal.reject()
            self._lock_screen()

    def _lock_screen(self):
        # H6-05: откладываем авто-блокировку при несохранённых правках ТОЛЬКО в
        # зашифрованном режиме — там lock обнуляет ключ, и плейн-черновики из
        # памяти были бы потеряны. Таймер не останавливаем: на следующем интервале
        # проверка повторится, и как только правок не останется — блокировка пройдёт.
        if self.db.encrypted and (self.is_editing or self._dirty_ids
                                  or self._edit_cache):
            self.statusBar().showMessage(
                "Авто-блокировка отложена: есть несохранённые изменения.", 4000)
            return
        self._idle_timer.stop()
        # Гасим незавершённый async карточки/галереи ДО закрытия/смены сессии БД:
        # висящие загрузки/сохранения/предпросмотр не должны примениться к UI уже
        # заблокированной или переоткрытой сессии (H65-02).
        self._quiesce_card_async()
        # В обычном (plaintext) режиме «блокировка» лишь скрывает карточку с экрана:
        # ключ не теряется, поэтому несохранённые правки безопасно стэшим в кеш и
        # восстанавливаем при возврате (H5-03). Стэшим черновик ТЕКУЩЕГО открытого
        # типа — аккаунт, фин-запись или сервер открыты не одновременно (одна
        # правая панель), маршрутизация как в _save_unsaved_before_exit. В
        # encrypted сюда попадаем уже без правок (см. ранний выход выше), так что
        # стэш — no-op (H-01: раньше стэшился только аккаунт, фин-/VPS-черновик
        # терялся молча).
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
        self._show_placeholder()
        # H-01: сбрасываем ВСЕ три current-состояния и их data-объекты —
        # симметрично _hide_fin_everywhere/_hide_servers_everywhere. Иначе
        # секреты открытой фин-/VPS-карточки оставались бы в памяти, и
        # карточка считалась бы всё ещё «открытой» (_any_card_open) сразу
        # после блокировки.
        self._current_account_id = None
        self._current_fin = None
        self.current_fin_data = None
        self._current_server = None
        self.current_server_data = None
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
        if not self.vault.wait_idle():
            theme.themed_info(
                self.config, self, "Блокировка отменена",
                "Фоновое сохранение базы не завершилось вовремя.\n"
                "Блокировка отменена, данные остались доступны.",
            )
            return
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
        sounds.play(self.config, "lock")
        self.status_panels.set_locked(True)
        # Сюда попадаем только без несохранённых правок (см. _lock_screen, H6-05),
        # поэтому кеш уже пуст — отдельная очистка не нужна. Снимаем лишь карточку
        # из памяти (гигиена секретов).
        self.current_account_data = None
        self._unlocking = True
        try:
            ok = self._open_database(self)
        finally:
            self._unlocking = False
        if not ok:
            self.close()
            return
        self.status_panels.set_locked(False)
        # Валидация/миграция схемы после разблокировки (единый путь с рестартом,
        # H65-05): _open_database открывает только соединение и схему не трогает.
        self._create_tables_or_exit()
        self._reload_tree()
        self._last_activity = QDateTime.currentDateTime()
        self._idle_timer.start(self._idle_timer.interval() or 30000)
        self.statusBar().showMessage("База разблокирована.", 3000)
        sounds.play(self.config, "unlock")

    # ─── Скриншот-защита ─────────────────────────────────────────────────────

    def _apply_screenshot_protect(self, enabled: bool):
        """Скриншот-защита главного окна. Результат WinAPI проверяется (M-01):
        при включении, которое НЕ применилось, пользователь видит честный
        статус, а не молчаливое ложное чувство защиты."""
        ok = theme.apply_screenshot_protect(self, enabled)
        if enabled and not ok:
            self.statusBar().showMessage(
                "ВНИМАНИЕ: защита от скриншотов НЕ применилась (см. лог).", 8000)
