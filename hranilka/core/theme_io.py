"""Экспорт/импорт темы оформления в JSON (7.7). Qt-free.

Файл содержит только ключи оформления (цвета, шрифт, chrome_style, звуки,
иконки) — никаких секретов и путей. Формат версионирован; неизвестные ключи
при импорте игнорируются, невалидные значения пропускаются и попадают в
``ThemeData.skipped``. Битый/чужой файл -> ThemeIOError с понятным текстом."""
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from hranilka.core.config import (_ENUMS, _HEX_COLOR_RE, _INT_RANGES, RETRO_THEMES,
                                  _coerce_bool)

logger = logging.getLogger(__name__)

FORMAT_ID = "hranilka-theme"
FORMAT_VERSION = 1
MAX_FILE_BYTES = 64 * 1024      # тема — крошечная; больше — не наш файл

_TREE_ICONS = ("text", "pixel")
_MAX_FONT_LEN = 100

# Явная схема: ключ -> тип значения. Только оформление.
THEME_KEYS: tuple[str, ...] = (
    "font", "font_size", "selected_theme",
    "text_color", "tree_bg_color", "main_bg_color",
    "chrome_style", "crt_mode",
    "sounds_enabled", "sound_volume",
    "sound_on_error", "sound_on_unlock", "sound_on_lock",
    "sound_on_copy", "sound_on_save",
    "tree_icons",
)
_COLOR_KEYS = ("text_color", "tree_bg_color", "main_bg_color")
_BOOL_KEYS = ("crt_mode", "sounds_enabled", "sound_on_error", "sound_on_unlock",
              "sound_on_lock", "sound_on_copy", "sound_on_save")


class ThemeIOError(Exception):
    """Файл темы нечитаем/не тот формат/пуст — сообщение пригодно для показа."""


@dataclass
class ThemeData:
    values: dict[str, Any] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)   # ключи с невалидным значением


_MISSING = object()


def _valid_font(v: Any) -> Any:
    if isinstance(v, str) and v.strip() and len(v) <= _MAX_FONT_LEN \
            and not any(ord(c) < 32 for c in v):
        return v.strip()
    return _MISSING


def _valid_color(v: Any) -> Any:
    return v.upper() if isinstance(v, str) and _HEX_COLOR_RE.match(v) else _MISSING


def _valid_int(key: str) -> Callable[[Any], Any]:
    lo, hi = _INT_RANGES[key]

    def check(v: Any) -> Any:
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            return _MISSING
        try:
            n = int(v)
        except (TypeError, ValueError):
            return _MISSING
        return max(lo, min(hi, n))
    return check


def _valid_enum(allowed: Any) -> Callable[[Any], Any]:
    return lambda v: v if isinstance(v, str) and v in allowed else _MISSING


def _valid_bool(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    r = _coerce_bool(v, None)       # type: ignore[arg-type]
    return _MISSING if r is None else r


def _validator(key: str) -> Callable[[Any], Any]:
    if key == "font":
        return _valid_font
    if key in _COLOR_KEYS:
        return _valid_color
    if key == "selected_theme":
        return _valid_enum(RETRO_THEMES)
    if key == "tree_icons":
        return _valid_enum(_TREE_ICONS)
    if key in _ENUMS:
        return _valid_enum(_ENUMS[key])
    if key in _INT_RANGES:
        return _valid_int(key)
    return _valid_bool


def validate(raw: Mapping[str, Any]) -> ThemeData:
    """Оставляет только известные ключи темы с валидными значениями."""
    data = ThemeData()
    for key in THEME_KEYS:
        if key not in raw:
            continue
        val = _validator(key)(raw[key])
        if val is _MISSING:
            data.skipped.append(key)
        else:
            data.values[key] = val
    return data


def dump(values: Mapping[str, Any]) -> str:
    """Сериализует значения темы (только известные и валидные ключи)."""
    clean = validate(values).values
    doc = {"format": FORMAT_ID, "version": FORMAT_VERSION, "theme": clean}
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load(text: str) -> ThemeData:
    """Разбирает JSON темы. ThemeIOError при битом файле/чужом формате/без
    единого валидного ключа."""
    try:
        doc = json.loads(text)
    except (ValueError, RecursionError) as e:
        raise ThemeIOError(f"Файл повреждён: это не корректный JSON ({e}).") from e
    if not isinstance(doc, dict) or doc.get("format") != FORMAT_ID:
        raise ThemeIOError("Это не файл темы Хранилки.")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ThemeIOError("В файле темы не указана версия формата.")
    if version > FORMAT_VERSION:
        raise ThemeIOError(
            f"Файл создан более новой версией программы (формат {version}).")
    theme = doc.get("theme")
    if not isinstance(theme, dict):
        raise ThemeIOError("В файле темы нет раздела «theme».")
    data = validate(theme)
    if not data.values:
        raise ThemeIOError("В файле нет ни одного корректного параметра темы.")
    return data


def write_text_atomic(path: "str | Path", text: str) -> None:
    """Запись через временный файл рядом с целью + os.replace: файл либо
    появляется целиком, либо не меняется. Временный файл всегда убирается."""
    target = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent),
                                    prefix=target.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except OSError:
        logger.error("Экспорт темы в %s не удался", target.name, exc_info=True)
        raise
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                logger.warning("Не удалось удалить временный файл %s", tmp.name)


def export_file(path: "str | Path", values: Mapping[str, Any]) -> None:
    write_text_atomic(path, dump(values))


def import_file(path: "str | Path") -> ThemeData:
    p = Path(path)
    try:
        size = p.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ThemeIOError("Файл слишком большой для файла темы.")
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ThemeIOError("Файл не является текстовым JSON (UTF-8).") from e
    except OSError as e:
        raise ThemeIOError(f"Не удалось прочитать файл: {e.strerror or e}.") from e
    return load(text)

