"""theme_io (7.7): экспорт/импорт темы в JSON, Qt-free."""
import json

import pytest

from hranilka.core import theme_io
from hranilka.core.theme_io import ThemeIOError

VALID = {
    "font": "Consolas", "font_size": 16, "selected_theme": "Matrix",
    "text_color": "#00ff00", "tree_bg_color": "#0D0D0D", "main_bg_color": "#1A1A1A",
    "chrome_style": "win98", "crt_mode": True,
    "sounds_enabled": False, "sound_volume": 30, "sound_on_error": True,
    "sound_on_unlock": False, "sound_on_lock": False,
    "sound_on_copy": True, "sound_on_save": False,
    "tree_icons": "pixel",
}


def test_roundtrip_via_file(tmp_path):
    p = tmp_path / "t.json"
    theme_io.export_file(p, VALID)
    data = theme_io.import_file(p)
    expected = dict(VALID, text_color="#00FF00")      # цвета нормализуются в верхний регистр
    assert data.values == expected
    assert data.skipped == []
    assert not list(tmp_path.glob("*.tmp"))


def test_dump_has_format_and_version_and_no_foreign_keys():
    raw = dict(VALID, password="secret", backup_folder="C:/x", shortcuts={"a": 1})
    doc = json.loads(theme_io.dump(raw))
    assert doc["format"] == theme_io.FORMAT_ID
    assert doc["version"] == theme_io.FORMAT_VERSION
    assert set(doc["theme"]) <= set(theme_io.THEME_KEYS)
    assert "password" not in doc["theme"] and "backup_folder" not in doc["theme"]


def test_unknown_keys_ignored():
    text = json.dumps({"format": "hranilka-theme", "version": 1,
                       "theme": {"text_color": "#112233", "evil": 1, "encryption_enabled": True}})
    assert theme_io.load(text).values == {"text_color": "#112233"}


@pytest.mark.parametrize("key,bad", [
    ("text_color", "red"), ("text_color", "#12345"), ("text_color", 5),
    ("chrome_style", "win3000"), ("selected_theme", "Nope"),
    ("font_size", "abc"), ("font_size", True), ("font", ""), ("font", "a\nb"),
    ("crt_mode", "maybe"), ("tree_icons", "bitmap"),
])
def test_invalid_values_skipped(key, bad):
    text = json.dumps({"format": "hranilka-theme", "version": 1,
                       "theme": {key: bad, "main_bg_color": "#000000"}})
    data = theme_io.load(text)
    assert key in data.skipped
    assert key not in data.values
    assert data.values["main_bg_color"] == "#000000"


def test_numbers_clamped():
    text = json.dumps({"format": "hranilka-theme", "version": 1,
                       "theme": {"font_size": 9999, "sound_volume": -5}})
    v = theme_io.load(text).values
    assert v["font_size"] == 96 and v["sound_volume"] == 0


@pytest.mark.parametrize("text", [
    "", "{", "not json", "[1, 2]", "null",
    '{"format": "other", "version": 1, "theme": {}}',
    '{"format": "hranilka-theme", "theme": {"text_color": "#000000"}}',
    '{"format": "hranilka-theme", "version": 1, "theme": []}',
    '{"format": "hranilka-theme", "version": 1, "theme": {}}',
    '{"format": "hranilka-theme", "version": 1, "theme": {"text_color": "bad"}}',
    '{"format": "hranilka-theme", "version": 99, "theme": {"text_color": "#000000"}}',
])
def test_broken_files_raise_clear_error(text):
    with pytest.raises(ThemeIOError) as ei:
        theme_io.load(text)
    assert str(ei.value)


def test_import_missing_and_binary_and_huge(tmp_path):
    with pytest.raises(ThemeIOError):
        theme_io.import_file(tmp_path / "nope.json")
    b = tmp_path / "b.json"
    b.write_bytes(b"\xff\xfe\x00\x80")
    with pytest.raises(ThemeIOError):
        theme_io.import_file(b)
    big = tmp_path / "big.json"
    big.write_text(" " * (theme_io.MAX_FILE_BYTES + 1), encoding="utf-8")
    with pytest.raises(ThemeIOError):
        theme_io.import_file(big)


def test_atomic_write_keeps_old_file_on_error(tmp_path, monkeypatch):
    p = tmp_path / "t.json"
    p.write_text("old", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(theme_io.os, "replace", boom)
    with pytest.raises(OSError):
        theme_io.write_text_atomic(p, "new")
    assert p.read_text(encoding="utf-8") == "old"
    assert [f.name for f in tmp_path.iterdir()] == ["t.json"]
