import json
import logging
import os

from paths import BASE_DIR

logger = logging.getLogger(__name__)

CONFIG_FILE = BASE_DIR / "config.json"

RETRO_THEMES = {
    "MS-DOS": {"text": "#00FF00", "tree_bg": "#000000", "main_bg": "#000000"},
    "Windows 95": {"text": "#000000", "tree_bg": "#C0C0C0", "main_bg": "#C0C0C0"},
    "Matrix": {"text": "#00FF00", "tree_bg": "#0D0D0D", "main_bg": "#1A1A1A"},
    "Amber Monitor": {"text": "#FFB000", "tree_bg": "#1A0F00", "main_bg": "#261800"},
    "Green Terminal": {"text": "#33FF33", "tree_bg": "#0A1F0A", "main_bg": "#0D260D"},
    "Blue Screen": {"text": "#FFFFFF", "tree_bg": "#0000AA", "main_bg": "#0000AA"},
    "Cyberpunk": {"text": "#00FFFF", "tree_bg": "#1A0033", "main_bg": "#260040"},
    "Classic IDE": {"text": "#000000", "tree_bg": "#FFFFFF", "main_bg": "#F0F0F0"}
}

class Config:
    def __init__(self):
        self.config = {
            "font": "Cascadia Code",
            "font_size": 14,
            "selected_theme": "Classic IDE",
            "text_color": "#000000",
            "tree_bg_color": "#FFFFFF",
            "main_bg_color": "#F0F0F0",
            "remember_geometry": False,
            "clipboard_clear_secs": 0,
            "clipboard_clear_on_exit": False,
            "recycle_bin_enabled": False,
            "screenshot_protect": False,
            "idle_lock_mins": 0,
            "backup_folder": "",
            "backup_auto_on_close": False,
            "backup_keep_count": 5,
            "warn_on_exit_unsaved": True,
            "encryption_enabled": False,
            "argon2_preset": "balanced",
            "shortcuts": {},
        }
        self.load()
    
    def load(self):
        if not CONFIG_FILE.exists():
            return
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                "Не удалось прочитать %s (%s). Используются настройки по умолчанию.",
                CONFIG_FILE, e,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("Повреждён %s (ожидался объект). Используются настройки "
                           "по умолчанию.", CONFIG_FILE)
            return
        self.config.update(self._sanitize(raw))

    def _sanitize(self, raw):
        """Приводит значения из файла к типам значений по умолчанию.

        config.json — редактируемый пользователем файл; некорректный тип (строка
        вместо числа и т. п.) раньше попадал прямо в таймеры/арифметику UI и мог
        уронить запуск. Для известных ключей приводим тип к дефолтному (числа —
        с отсечением отрицательных), при неудаче берём дефолт. Неизвестные ключи
        (служебные, напр. _window_geometry) сохраняем как есть."""
        clean = {}
        for key, value in raw.items():
            if key not in self.config:
                clean[key] = value           # служебные/неизвестные — без изменений
                continue
            default = self.config[key]
            if isinstance(default, bool):    # bool раньше int (bool — подтип int)
                clean[key] = bool(value)
            elif isinstance(default, int):
                try:
                    clean[key] = max(0, int(value))
                except (TypeError, ValueError):
                    clean[key] = default
            elif isinstance(default, str):
                clean[key] = value if isinstance(value, str) else default
            elif isinstance(default, dict):
                clean[key] = value if isinstance(value, dict) else default
            else:
                clean[key] = value
        return clean
    
    def save(self):
        # Атомарная запись: пишем во временный файл и подменяем им основной,
        # чтобы обрыв на середине не повредил config.json.
        tmp = str(CONFIG_FILE) + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_FILE)
    
    def get(self, key, default=None):
        return self.config.get(key, default)
    
    def set(self, key, value):
        self.config[key] = value