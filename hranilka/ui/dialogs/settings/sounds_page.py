"""Вкладка «Звуки»: включение, громкость и события с проверкой звучания.
Часть SettingsDialog (dialog.py)."""
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton, QLabel, QCheckBox, QWidget)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSlider
from hranilka.ui import sounds


class SettingsSoundsMixin:
    # (событие, подпись) — ключи config: sound_on_<событие>
    _SOUND_EVENTS = [
        ("error", "Ошибка / предупреждение"),
        ("unlock", "Разблокировка"),
        ("lock", "Блокировка"),
        ("copy", "Копирование в буфер"),
        ("save", "Успешное сохранение"),
    ]

    def _page_sounds(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.addWidget(self._build_sounds_group())
        lay.addStretch()
        return w

    def _build_sounds_group(self):
        group = QGroupBox("Звуки")
        gl = QVBoxLayout(group)
        self.sounds_enabled_check = QCheckBox("Включить звуки")
        self.sounds_enabled_check.setChecked(self.config.get("sounds_enabled", True))
        gl.addWidget(self.sounds_enabled_check)

        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("Громкость:"))
        self.sound_volume_slider = QSlider(Qt.Horizontal)
        self.sound_volume_slider.setRange(0, 100)
        self.sound_volume_slider.setValue(int(self.config.get("sound_volume", 40)))
        vol_row.addWidget(self.sound_volume_slider, 1)
        self.sound_volume_value = QLabel(str(self.sound_volume_slider.value()))
        self.sound_volume_value.setMinimumWidth(36)
        vol_row.addWidget(self.sound_volume_value)
        self.sound_volume_slider.valueChanged.connect(
            lambda v: self.sound_volume_value.setText(str(v)))
        gl.addLayout(vol_row)

        self.sound_event_checks = {}
        for event, caption in self._SOUND_EVENTS:
            row = QHBoxLayout()
            box = QCheckBox(caption)
            box.setChecked(bool(self.config.get(sounds.config_key(event),
                                                event == "error")))
            self.sound_event_checks[event] = box
            row.addWidget(box, 1)
            test = QPushButton("Проверить")
            test.clicked.connect(
                lambda _=False, ev=event: sounds.play(
                    self.config, ev, force=True,
                    volume=self.sound_volume_slider.value()))
            row.addWidget(test)
            gl.addLayout(row)

        def _sync(on: bool) -> None:
            self.sound_volume_slider.setEnabled(on)
            for b in self.sound_event_checks.values():
                b.setEnabled(on)
        self.sounds_enabled_check.toggled.connect(_sync)
        _sync(self.sounds_enabled_check.isChecked())
        return group

    def _apply_sound_settings(self) -> None:
        self.config.set("sounds_enabled", self.sounds_enabled_check.isChecked())
        self.config.set("sound_volume", self.sound_volume_slider.value())
        for event, box in self.sound_event_checks.items():
            self.config.set(sounds.config_key(event), box.isChecked())
