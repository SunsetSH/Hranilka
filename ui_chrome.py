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

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QEvent, QDateTime, QByteArray
from PySide6.QtGui import QKeySequence

import theme
import shortcuts
from database import VaultConflictError


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
        return super().nativeEvent(event_type, message)

    def _restore_geometry(self):
        """Восстановить размер/положение окна (только при старте)."""
        if self.config.get("remember_geometry"):
            geo_hex = self.config.get("_window_geometry", "")
            if geo_hex:
                self.restoreGeometry(QByteArray.fromHex(bytes(geo_hex, "ascii")))

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
        # Несохранённые правки текущей карточки не теряем при авто-блокировке
        # (H5-03): стэшим их в кеш до показа заглушки — так же, как при обычном
        # переключении между аккаунтами (on_item_selected).
        if self.is_editing and self._current_account_id is not None:
            self._stash_current_edits(self._current_account_id)
            self._refresh_dirty_markers()
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
