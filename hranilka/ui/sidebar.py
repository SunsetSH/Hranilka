"""Боковая панель главного окна (SidebarMixin): кнопки создания узлов и
видимость секций «финансы»/«серверы» по настройкам."""
import logging

from PySide6.QtWidgets import QPushButton

from hranilka.core.nodetypes import FIN_LEAF_TYPES, SERVER


class SidebarMixin:
    def _apply_empty_card_visibility(self):
        """Обновить пустые поля/вкладки только у открытой карточки."""
        current = self.right_stack.currentWidget()
        if current is self.tabs:
            self.tabs.refresh_empty_visibility(self.is_editing)
            return
        if current is self.server_tabs:
            self.server_tabs.refresh_empty_visibility(self.is_editing)
            return
        for fin_tabs in self.fin_tabs_by_type.values():
            if current is fin_tabs:
                fin_tabs.refresh_empty_visibility(self.is_editing)
                return

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

