from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QScrollArea)
from PySide6.QtCore import Qt
from flowlayout import WrappingTabWidget
from widgets import (CopyableField, CopyableDateField, CopyableTextEdit,
                     SecretQuestionsWidget, CodeListWidget, GalleryWidget,
                     IntervalField, LinkedAccountsWidget, heading_label)


class AccountTabs(WrappingTabWidget):
    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.config = config
        self.build_tabs()

    def build_tabs(self):
        self.addTab(self.create_tab_baza(), "База")
        self.addTab(self.create_tab_login(), "Логин и пароль")
        self.addTab(self.create_tab_pd(), "Персональные данные")
        self.addTab(self.create_tab_questions(), "Секретный вопрос")
        self.addTab(self.create_tab_recovery(), "Фраза восстановления")
        self.addTab(self.create_tab_codes(), "Резерв 2FA")
        self.addTab(self.create_tab_gallery(), "Галерея")
        self.addTab(self.create_tab_tech(), "Технические данные")

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

        l.addWidget(heading_label("Название аккаунта:"))
        l.addWidget(self.f_name)
        l.addWidget(heading_label("Адрес сайта (URL):"))
        l.addWidget(self.f_url)
        l.addWidget(heading_label("Дата создания:"))
        l.addWidget(self.f_creation_date)
        l.addWidget(heading_label("Заметки:"))
        l.addWidget(self.f_notes)
        l.addWidget(heading_label("Связанные аккаунты:"))
        l.addWidget(self.f_linked)
        l.addStretch()
        return w

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

        l.addWidget(heading_label("Логин:"))
        l.addWidget(self.f_login)
        l.addWidget(heading_label("Пароль:"))
        l.addWidget(self.f_password)
        gen_row = QHBoxLayout()
        gen_row.setSpacing(5)
        gen_row.addWidget(self.gen_pass_btn)
        gen_row.addWidget(self.gen_pass_cfg_btn)
        l.addLayout(gen_row)
        l.addWidget(heading_label("Пароль сменён:"))
        l.addWidget(self.f_password_date)
        l.addWidget(heading_label("Сменять пароль каждые:"))
        l.addWidget(self.f_pwd_interval)
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

        l.addWidget(heading_label("Мобильный номер:"))
        l.addWidget(self.f_mobile)
        l.addWidget(heading_label("Имя:"))
        l.addWidget(self.f_first)
        l.addWidget(heading_label("Фамилия:"))
        l.addWidget(self.f_last)
        l.addWidget(heading_label("Отчество:"))
        l.addWidget(self.f_middle)
        l.addWidget(heading_label("Дата рождения:"))
        l.addWidget(self.f_birth)
        l.addWidget(heading_label("Адрес:"))
        l.addWidget(self.f_address)
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
        return w

    def create_tab_recovery(self):
        w = QWidget(self)
        l = QVBoxLayout(w)
        l.setSpacing(10)
        l.setContentsMargins(0, 8, 0, 0)
        self.f_recovery = CopyableTextEdit()
        self.f_device_id = CopyableField()
        l.addWidget(heading_label("Фраза восстановления:"))
        l.addWidget(self.f_recovery)
        l.addWidget(heading_label("ID устройства:"))
        l.addWidget(self.f_device_id)
        l.addStretch()
        return w

    def _scrollable(self, inner):
        """Обернуть виджет в прокручиваемую область: при большом числе элементов
        (галерея, коды 2FA) появляется вертикальная прокрутка, а не сжатие строк
        (Баг 5). Окно при этом не растягивается."""
        sa = QScrollArea(self)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QScrollArea.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setWidget(inner)
        return sa

    def apply_scroll_bg(self, main_bg: str) -> None:
        """Обновить фон прокручиваемых областей вкладок под цвет из настроек."""
        style = f"QScrollArea {{ background: {main_bg}; border: none; }}"
        vp_style = f"background: {main_bg};"
        for sa in (self._sa_codes, self._sa_gallery):
            sa.setStyleSheet(style)
            sa.viewport().setStyleSheet(vp_style)

    def create_tab_codes(self):
        self.f_codes_widget = CodeListWidget(config=self.config, parent=self)
        self._sa_codes = self._scrollable(self.f_codes_widget)
        return self._sa_codes

    def create_tab_gallery(self):
        self.f_gallery_widget = GalleryWidget(config=self.config, parent=self)
        self._sa_gallery = self._scrollable(self.f_gallery_widget)
        return self._sa_gallery

    def create_tab_tech(self):
        w = QWidget(self)
        l = QVBoxLayout(w)
        l.setSpacing(10)
        l.setContentsMargins(0, 8, 0, 0)
        self.f_ip = CopyableField()
        self.f_browser = CopyableField()
        self.f_browser.set_placeholder("Chrome/Firefox/...")
        self.f_os = CopyableField()
        l.addWidget(heading_label("IP адрес:"))
        l.addWidget(self.f_ip)
        l.addWidget(heading_label("Браузер:"))
        l.addWidget(self.f_browser)
        l.addWidget(heading_label("ОС:"))
        l.addWidget(self.f_os)
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

        self.gen_pass_btn.setVisible(editable)
        self.gen_pass_cfg_btn.setVisible(editable)
        self.gen_pd_btn.setVisible(editable)

        self.f_questions_widget.set_editable(editable)
        self.f_codes_widget.set_editable(editable)
        self.f_gallery_widget.set_editable(editable)
