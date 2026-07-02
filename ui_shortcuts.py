"""Подсистема горячих клавиш главного окна (Эпик 4.4).

Вынесена из MainWindow как примесь (mixin): методы разделяют общее состояние
окна через self, поэтому поведение идентично прежнему, а god-object меньше.
Подмешивается в MainWindow перед QMainWindow в списке баз."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence

import shortcuts


class ShortcutsMixin:
    """Создание/пере-привязка QShortcut'ов и диспетчеризация действий."""

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
        elif self.search_box.hasFocus() and self.search_box.text():
            # Вне режима правки WindowShortcut «съедал» Esc, и стандартная
            # очистка поля поиска не срабатывала — делаем её явно (L-14).
            self.search_box.clear()

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
