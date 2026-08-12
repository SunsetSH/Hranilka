from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QScrollArea)
from PySide6.QtCore import Qt
from hranilka.ui.flowlayout import WrappingTabWidget
from hranilka.ui.widgets import (CopyableField, CopyableDateField, CopyableTextEdit,
                     SecretQuestionsWidget, CodeListWidget, GalleryWidget,
                     IntervalField, LinkedAccountsWidget, LinkedFinItemsWidget,
                     LinkedServersWidget, MaskedTextEdit, heading_label)


# Отступ между содержимым вкладки и полосой прокрутки (~межэлементный отступ
# формы) — иначе поля примыкают вплотную к скроллбару (УИ §2026-07-15).
_SCROLL_CONTENT_MARGIN = 8


def wrap_scrollable(inner: QWidget, parent: QWidget | None = None) -> QScrollArea:
    """Обернуть содержимое вкладки в вертикально прокручиваемую область: при
    малом окне поля не сжимаются, а вкладка прокручивается (Баг 5). Только
    вертикальный скролл, окно при этом не растягивается. Общий хелпер для
    AccountTabs/FinItemTabs/ServerTabs — не копипастить обёртку по вкладкам.

    inner оборачивается в промежуточный контейнер с правым отступом
    (_SCROLL_CONTENT_MARGIN) от полосы прокрутки — системно для всех карточек,
    без правок в каждой отдельной вкладке."""
    sa = QScrollArea(parent)
    sa.setWidgetResizable(True)
    sa.setFrameShape(QScrollArea.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    container = QWidget(sa)
    outer = QVBoxLayout(container)
    outer.setContentsMargins(0, 0, _SCROLL_CONTENT_MARGIN, 0)
    outer.addWidget(inner)
    sa.setWidget(container)
    return sa


def apply_scroll_areas_bg(scroll_areas, main_bg: str) -> None:
    """Обновить фон прокручиваемых областей вкладок под цвет из настроек."""
    style = f"QScrollArea {{ background: {main_bg}; border: none; }}"
    vp_style = f"background: {main_bg};"
    for sa in scroll_areas:
        sa.setStyleSheet(style)
        sa.viewport().setStyleSheet(vp_style)


class AccountTabs(WrappingTabWidget):
    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.config = config
        self._scroll_areas: list[QScrollArea] = []
        self.build_tabs()

    def _add_scroll_tab(self, inner: QWidget, title: str) -> None:
        sa = wrap_scrollable(inner, self)
        self._scroll_areas.append(sa)
        index = self.addTab(sa, title)
        self.bind_empty_page(inner, index)

    @staticmethod
    def _has_text(widget) -> bool:
        return bool(widget.get_text().strip())

    def _add_labeled_field(self, page, layout, title, widget,
                           has_content=None, available=None, required=False):
        """Добавить подпись + поле и зарегистрировать их как одну секцию."""
        label = heading_label(title)
        layout.addWidget(label)
        layout.addWidget(widget)
        predicate = has_content or (lambda w=widget: self._has_text(w))
        self.register_empty_section(
            page, (label, widget), predicate, available=available,
            required=required)
        return label

    def build_tabs(self):
        self._add_scroll_tab(self.create_tab_baza(), "База")
        self._add_scroll_tab(self.create_tab_login(), "Логин и пароль")
        self._add_scroll_tab(self.create_tab_pd(), "Персональные данные")
        self._add_scroll_tab(self.create_tab_questions(), "Секретный вопрос")
        self._add_scroll_tab(self.create_tab_recovery(), "Фраза восстановления")
        self._add_scroll_tab(self.create_tab_codes(), "Резерв 2FA")
        self._add_scroll_tab(self.create_tab_gallery(), "Галерея")
        self._add_scroll_tab(self.create_tab_tech(), "Технические данные")

    def create_tab_baza(self):
        w = QWidget(self)
        l = QVBoxLayout(w)
        l.setSpacing(10)
        l.setContentsMargins(0, 8, 0, 0)
        self.f_name = CopyableField()
        self.f_url = CopyableField()
        self.f_creation_date = CopyableDateField(is_datetime=True)
        self.f_notes = CopyableTextEdit()
        self.f_linked = LinkedAccountsWidget()
        self.f_fin_linked = LinkedFinItemsWidget()
        self.f_server_linked = LinkedServersWidget()

        self._add_labeled_field(
            w, l, "Название аккаунта (обязательно):", self.f_name,
            required=True)
        self._add_labeled_field(w, l, "Адрес сайта (URL):", self.f_url)
        self._add_labeled_field(
            w, l, "Дата создания:", self.f_creation_date,
            lambda: self.f_creation_date.get_date() is not None)
        self._add_labeled_field(w, l, "Заметки:", self.f_notes)
        self._add_labeled_field(
            w, l, "Связанные аккаунты:", self.f_linked,
            lambda: bool(self.f_linked.get_data()))
        # Секция привязанных карт/кошельков — скрывается при выключенной опции
        # «Показывать фин. инструменты» (заголовок + виджет, см.
        # set_fin_section_visible).
        self.f_fin_linked_heading = heading_label("ПРИВЯЗАННЫЕ КАРТЫ И КОШЕЛЬКИ:")
        l.addWidget(self.f_fin_linked_heading)
        l.addWidget(self.f_fin_linked)
        self.register_empty_section(
            w, (self.f_fin_linked_heading, self.f_fin_linked),
            lambda: bool(self.f_fin_linked.get_data()),
            available=lambda: bool(
                self.config is None
                or self.config.get("show_fin_instruments", False)))
        # Секция привязанных серверов — независимый аналог (docs/ТЗ_VPS_Серверы.md
        # §4), скрывается при выключенной опции «Показывать серверы».
        self.f_server_linked_heading = heading_label("ПРИВЯЗАННЫЕ СЕРВЕРЫ:")
        l.addWidget(self.f_server_linked_heading)
        l.addWidget(self.f_server_linked)
        self.register_empty_section(
            w, (self.f_server_linked_heading, self.f_server_linked),
            lambda: bool(self.f_server_linked.get_data()),
            available=lambda: bool(
                self.config is None or self.config.get("show_servers", False)))
        l.addStretch()
        return w

    def set_fin_section_visible(self, visible):
        """Показать/скрыть секцию «Привязанные карты и кошельки» на карточке
        аккаунта (заголовок + виджет)."""
        self.f_fin_linked_heading.setVisible(visible)
        self.f_fin_linked.setVisible(visible)

    def set_server_section_visible(self, visible):
        """Показать/скрыть секцию «Привязанные серверы» на карточке аккаунта
        (заголовок + виджет)."""
        self.f_server_linked_heading.setVisible(visible)
        self.f_server_linked.setVisible(visible)

    def create_tab_login(self):
        w = QWidget(self)
        l = QVBoxLayout(w)
        l.setSpacing(10)
        l.setContentsMargins(0, 8, 0, 0)
        self.f_login = CopyableField()
        self.f_password = CopyableField(is_password=True)
        self.gen_pass_btn = QPushButton("СГЕНЕРИРОВАТЬ ПАРОЛЬ")
        self.gen_pass_cfg_btn = QPushButton("ПАРАМЕТРЫ ГЕНЕРАЦИИ")
        self.gen_pass_cfg_btn.setToolTip("Настройки генерации пароля")
        self.f_password_date = CopyableDateField(is_datetime=False)
        self.f_pwd_interval = IntervalField()

        self._add_labeled_field(w, l, "Логин:", self.f_login)
        self._add_labeled_field(w, l, "Пароль:", self.f_password)
        gen_row = QHBoxLayout()
        gen_row.setSpacing(5)
        gen_row.addWidget(self.gen_pass_btn)
        gen_row.addWidget(self.gen_pass_cfg_btn)
        l.addLayout(gen_row)
        self._add_labeled_field(
            w, l, "Пароль сменён:", self.f_password_date,
            lambda: self.f_password_date.get_date() is not None)
        self._add_labeled_field(
            w, l, "Сменять пароль каждые:", self.f_pwd_interval,
            lambda: self.f_pwd_interval.get_value() is not None)
        l.addStretch()
        return w

    def create_tab_pd(self):
        w = QWidget(self)
        l = QVBoxLayout(w)
        l.setSpacing(10)
        l.setContentsMargins(0, 8, 0, 0)
        self.f_mobile = CopyableField()
        self.f_first = CopyableField()
        self.f_last = CopyableField()
        self.f_middle = CopyableField()
        self.f_birth = CopyableDateField(is_datetime=False)
        self.f_address = CopyableField()
        self.gen_pd_btn = QPushButton("СГЕНЕРИРОВАТЬ (RU/EN)")

        self._add_labeled_field(w, l, "Мобильный номер:", self.f_mobile)
        self._add_labeled_field(w, l, "Имя:", self.f_first)
        self._add_labeled_field(w, l, "Фамилия:", self.f_last)
        self._add_labeled_field(w, l, "Отчество:", self.f_middle)
        self._add_labeled_field(
            w, l, "Дата рождения:", self.f_birth,
            lambda: self.f_birth.get_date() is not None)
        self._add_labeled_field(w, l, "Адрес:", self.f_address)
        l.addWidget(self.gen_pd_btn)
        l.addStretch()
        return w

    def create_tab_questions(self):
        w = QWidget(self)
        l = QVBoxLayout(w)
        l.setSpacing(10)
        l.setContentsMargins(0, 8, 0, 0)
        self.f_questions_widget = SecretQuestionsWidget(config=self.config, parent=w)
        l.addWidget(self.f_questions_widget)
        self.register_empty_section(
            w, self.f_questions_widget,
            lambda: bool(self.f_questions_widget.get_data()))
        return w

    def create_tab_recovery(self):
        w = QWidget(self)
        l = QVBoxLayout(w)
        l.setSpacing(10)
        l.setContentsMargins(0, 8, 0, 0)
        # Секрет: в просмотре текст скрыт, раскрывается кнопкой (M-02).
        self.f_recovery = MaskedTextEdit()
        self.f_device_id = CopyableField()
        self._add_labeled_field(
            w, l, "Фраза восстановления:", self.f_recovery)
        self._add_labeled_field(w, l, "ID устройства:", self.f_device_id)
        l.addStretch()
        return w

    def apply_scroll_bg(self, main_bg: str) -> None:
        """Обновить фон прокручиваемых областей вкладок под цвет из настроек."""
        apply_scroll_areas_bg(self._scroll_areas, main_bg)

    def create_tab_codes(self):
        self.f_codes_widget = CodeListWidget(config=self.config, parent=self)
        self.register_empty_section(
            self.f_codes_widget, self.f_codes_widget,
            lambda: bool(self.f_codes_widget.get_data()))
        return self.f_codes_widget

    def create_tab_gallery(self):
        self.f_gallery_widget = GalleryWidget(config=self.config, parent=self)
        self.register_empty_section(
            self.f_gallery_widget, self.f_gallery_widget,
            lambda: bool(self.f_gallery_widget.items))
        return self.f_gallery_widget

    def create_tab_tech(self):
        w = QWidget(self)
        l = QVBoxLayout(w)
        l.setSpacing(10)
        l.setContentsMargins(0, 8, 0, 0)
        self.f_ip = CopyableField()
        self.f_browser = CopyableField()
        self.f_browser.set_placeholder("Chrome/Firefox/...")
        self.f_os = CopyableField()
        self._add_labeled_field(w, l, "IP адрес:", self.f_ip)
        self._add_labeled_field(w, l, "Браузер:", self.f_browser)
        self._add_labeled_field(w, l, "ОС:", self.f_os)
        l.addStretch()
        return w

    def set_all_editable(self, editable):
        fields = [self.f_name, self.f_url, self.f_login, self.f_password,
                  self.f_mobile, self.f_first, self.f_last, self.f_middle,
                  self.f_address, self.f_device_id, self.f_ip, self.f_browser,
                  self.f_os]
        for f in fields:
            f.set_editable(editable)

        self.f_notes.set_editable(editable)
        self.f_recovery.set_editable(editable)
        self.f_creation_date.set_editable(editable)
        self.f_password_date.set_editable(editable)
        self.f_birth.set_editable(editable)
        self.f_pwd_interval.set_editable(editable)
        self.f_linked.set_editable(editable)
        self.f_fin_linked.set_editable(editable)
        self.f_server_linked.set_editable(editable)

        self.gen_pass_btn.setVisible(editable)
        self.gen_pass_cfg_btn.setVisible(editable)
        self.gen_pd_btn.setVisible(editable)

        self.f_questions_widget.set_editable(editable)
        self.f_codes_widget.set_editable(editable)
        self.f_gallery_widget.set_editable(editable)
        self.refresh_empty_visibility(editable)
