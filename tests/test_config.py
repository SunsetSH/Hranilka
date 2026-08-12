"""M3-04: валидация config по явной схеме (типы/enum/диапазоны)."""
import json


def test_unknown_keys_dropped(pure_config):
    clean = pure_config._sanitize({"totally_unknown": 123, "font_size": 14})
    assert "totally_unknown" not in clean
    assert clean["font_size"] == 14


def test_bool_string_false_is_false(pure_config):
    # bool('false') == True — классический баг; должно стать False.
    assert pure_config._sanitize({"remember_geometry": "false"})["remember_geometry"] is False
    assert pure_config._sanitize({"remember_geometry": "true"})["remember_geometry"] is True
    assert pure_config._sanitize({"remember_geometry": 1})["remember_geometry"] is True


def test_hide_empty_card_fields_defaults_on_and_sanitizes(pure_config):
    assert pure_config.get("hide_empty_card_fields") is True
    assert pure_config._sanitize(
        {"hide_empty_card_fields": "false"})["hide_empty_card_fields"] is False


def test_int_ranges_clamped(pure_config):
    assert pure_config._sanitize({"font_size": "999"})["font_size"] == 96      # max
    assert pure_config._sanitize({"font_size": -10})["font_size"] == 6         # min
    assert pure_config._sanitize({"idle_lock_mins": -5})["idle_lock_mins"] == 0
    # мусор → дефолт
    assert pure_config._sanitize({"font_size": "abc"})["font_size"] == pure_config.config["font_size"]


def test_color_validation(pure_config):
    assert pure_config._sanitize({"text_color": "#1A2B3C"})["text_color"] == "#1A2B3C"
    # некорректный цвет → дефолт
    assert pure_config._sanitize({"text_color": "red"})["text_color"] == pure_config.config["text_color"]
    assert pure_config._sanitize({"text_color": "#12345"})["text_color"] == pure_config.config["text_color"]


def test_enum_validation(pure_config):
    assert pure_config._sanitize({"argon2_preset": "paranoid"})["argon2_preset"] == "paranoid"
    assert pure_config._sanitize({"argon2_preset": "hacker"})["argon2_preset"] == pure_config.config["argon2_preset"]
    assert pure_config._sanitize({"sort_mode": "created"})["sort_mode"] == "created"
    assert pure_config._sanitize({"sort_mode": "weird"})["sort_mode"] == "manual"
    # gallery_thumb_preload: только startup/on_click, мусор → дефолт startup.
    assert pure_config._sanitize(
        {"gallery_thumb_preload": "on_click"})["gallery_thumb_preload"] == "on_click"
    assert pure_config._sanitize(
        {"gallery_thumb_preload": "nope"})["gallery_thumb_preload"] == "startup"


def test_window_geometry_must_be_str(pure_config):
    # список раньше ронял старт (QByteArray.fromHex) — теперь падает на дефолт "".
    assert pure_config._sanitize({"_window_geometry": ["a", "b"]})["_window_geometry"] == ""
    assert pure_config._sanitize({"_window_geometry": "deadbeef"})["_window_geometry"] == "deadbeef"


# ─── L-15: save()/load() round-trip и санитизация файла на load ───────────────

def test_save_load_roundtrip(tmp_path, monkeypatch):
    """Значения переживают save→load через файл (CONFIG_FILE монкипатчится)."""
    from hranilka.core import config
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_file)

    c = config.Config()
    c.set("font_size", 22)
    c.set("text_color", "#123456")
    c.set("argon2_preset", "paranoid")
    c.set("remember_geometry", True)
    c.save()
    assert cfg_file.exists()

    c2 = config.Config()                     # читает тот же файл
    assert c2.get("font_size") == 22
    assert c2.get("text_color") == "#123456"
    assert c2.get("argon2_preset") == "paranoid"
    assert c2.get("remember_geometry") is True


def test_load_sanitizes_invalid_file_values(tmp_path, monkeypatch):
    """Некорректные значения в файле чинятся на load: диапазоны/enum/цвет → дефолт,
    неизвестные ключи отбрасываются."""
    from hranilka.core import config
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_file)

    cfg_file.write_text(json.dumps({
        "font_size": 999,                    # > max → клампится к 96
        "text_color": "not-a-color",         # некорректный → дефолт
        "argon2_preset": "hacker",           # вне enum → дефолт
        "totally_unknown": 1,                # неизвестный → отброшен
    }), encoding="utf-8")

    c = config.Config()
    assert c.get("font_size") == 96
    assert c.get("text_color") == "#000000"          # дефолт
    assert c.get("argon2_preset") == "balanced"      # дефолт
    assert "totally_unknown" not in c.config
