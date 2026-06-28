"""M3-04: валидация config по явной схеме (типы/enum/диапазоны)."""


def test_unknown_keys_dropped(pure_config):
    clean = pure_config._sanitize({"totally_unknown": 123, "font_size": 14})
    assert "totally_unknown" not in clean
    assert clean["font_size"] == 14


def test_bool_string_false_is_false(pure_config):
    # bool('false') == True — классический баг; должно стать False.
    assert pure_config._sanitize({"remember_geometry": "false"})["remember_geometry"] is False
    assert pure_config._sanitize({"remember_geometry": "true"})["remember_geometry"] is True
    assert pure_config._sanitize({"remember_geometry": 1})["remember_geometry"] is True


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


def test_window_geometry_must_be_str(pure_config):
    # список раньше ронял старт (QByteArray.fromHex) — теперь падает на дефолт "".
    assert pure_config._sanitize({"_window_geometry": ["a", "b"]})["_window_geometry"] == ""
    assert pure_config._sanitize({"_window_geometry": "deadbeef"})["_window_geometry"] == "deadbeef"
