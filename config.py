import json
import logging
import os
import re
from typing import Any

from paths import BASE_DIR

logger = logging.getLogger(__name__)

CONFIG_FILE = BASE_DIR / "config.json"

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Явная схема валидации значений config.json (M3-04). config.json редактируется
# пользователем — некорректный тип/диапазон раньше попадал прямо в таймеры,
# арифметику UI и QByteArray.fromHex и мог уронить запуск. Для каждого
# потребляемого ключа задаём точные правила; неизвестные ключи игнорируются.
_COLOR_KEYS = {"text_color", "tree_bg_color", "main_bg_color"}
# key -> (min, max) для целых значений
_INT_RANGES = {
    "font_size": (6, 96),
    "clipboard_clear_secs": (0, 86400),     # 0..24 ч
    "idle_lock_mins": (0, 10080),           # 0..7 сут
    "backup_keep_count": (0, 10000),
}
# key -> допустимое множество значений
_ENUMS = {
    "argon2_preset": {"fast", "balanced", "paranoid"},
    "sort_mode": {"manual", "name", "created", "pwd_due"},
    "gallery_thumb_preload": {"startup", "on_click"},
}


def _coerce_bool(value: Any, default: bool) -> bool:
    """Строгое приведение к bool: bool('false') больше НЕ даёт True (M3-04)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off", ""):
            return False
    return default

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
    def __init__(self) -> None:
        self.config: dict[str, Any] = {
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
            "welcome_shown": False,        # обучение при первом запуске уже показано
            "encryption_enabled": False,
            "argon2_preset": "balanced",
            "image_downscale": True,       # сжимать большие изображения при импорте
            # Когда подгружать миниатюры галереи: "startup" — все сразу при
            # открытии карточки (пик ОЗУ, см. M6-03), "on_click" — только BLOB
            # нажатого изображения (ленивее по памяти).
            "gallery_thumb_preload": "startup",
            "shortcuts": {},
            # Настройки генерации пароля/парольной фразы; поля словаря
            # валидирует password_gen.settings_from_config (мусор → дефолты).
            "password_gen": {},
            # Ранее «потреблялись», но отсутствовали в defaults и потому
            # проходили БЕЗ валидации (M3-04). Теперь известны и проверяются.
            "font_filter_mono": True,
            "sort_mode": "manual",
            "sort_desc": False,
            "_window_geometry": "",        # hex-строка QByteArray (или пусто)
        }
        self.load()
    
    def load(self) -> None:
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

    def _sanitize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Валидирует значения из файла по явной схеме (типы, enum, диапазоны).

        Известные ключи проверяются и при несоответствии заменяются дефолтом;
        неизвестные ключи ИГНОРИРУЮТСЯ (не попадают в конфиг) — так мусор/опечатки
        в config.json не доходят до кода (M3-04)."""
        clean = {}
        for key, value in raw.items():
            if key not in self.config:
                continue                      # неизвестные ключи отбрасываем
            clean[key] = self._coerce(key, value)
        return clean

    def _coerce(self, key: str, value: Any) -> Any:
        """Приводит одно значение к допустимому для ключа по схеме."""
        default = self.config[key]
        if key in _COLOR_KEYS:
            return value if isinstance(value, str) and _HEX_COLOR_RE.match(value) else default
        if key == "selected_theme":
            return value if value in RETRO_THEMES else default
        if key in _ENUMS:
            return value if value in _ENUMS[key] else default
        if key in _INT_RANGES:
            lo, hi = _INT_RANGES[key]
            try:
                return max(lo, min(hi, int(value)))
            except (TypeError, ValueError):
                return default
        if isinstance(default, bool):         # до int: bool — подтип int
            return _coerce_bool(value, default)
        if isinstance(default, int):
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return default
        if isinstance(default, str):
            return value if isinstance(value, str) else default
        if isinstance(default, dict):
            return value if isinstance(value, dict) else default
        return value
    
    def save(self) -> None:
        # Атомарная запись: пишем во временный файл и подменяем им основной,
        # чтобы обрыв на середине не повредил config.json.
        tmp = str(CONFIG_FILE) + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_FILE)
    
    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.config[key] = value