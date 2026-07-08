"""Генерация паролей/парольных фраз (password_gen) и диалог настроек
(ui_generator.GeneratorSettingsDialog).
"""
import string

import pytest

import password_gen
from password_gen import (GeneratorSettings, PassphraseOptions,
                          PasswordOptions, generate_passphrase,
                          generate_password, settings_from_config,
                          settings_to_config)


# ─── Пароль ──────────────────────────────────────────────────────────────────

def test_password_default_length_and_charset():
    pw = generate_password(PasswordOptions())
    assert len(pw) == 16
    allowed = set(string.ascii_letters + string.digits + "!@#$%^&*")
    assert set(pw) <= allowed


def test_password_min_counts_guaranteed():
    opts = PasswordOptions(length=12, min_digits=5, min_symbols=4)
    for _ in range(20):
        pw = generate_password(opts)
        assert sum(c.isdigit() for c in pw) >= 5
        assert sum(c in "!@#$%^&*" for c in pw) >= 4


def test_password_only_selected_charsets():
    opts = PasswordOptions(use_upper=False, use_symbols=False,
                           min_symbols=0, length=64)
    pw = generate_password(opts)
    assert set(pw) <= set(string.ascii_lowercase + string.digits)


def test_password_exclude_similar():
    opts = PasswordOptions(length=128, exclude_similar=True)
    for _ in range(10):
        assert not set(generate_password(opts)) & set("0O1lI")


def test_password_no_charsets_raises():
    opts = PasswordOptions(use_upper=False, use_lower=False,
                           use_digits=False, use_symbols=False)
    with pytest.raises(ValueError):
        generate_password(opts)


def test_password_mins_clamped_to_length():
    # Минимумы 10+10 не влезают в длину 5 — генерация не падает.
    opts = PasswordOptions(length=5, min_digits=10, min_symbols=10)
    assert len(generate_password(opts)) == 5


def test_password_disabled_set_zeroes_its_min():
    opts = PasswordOptions(use_digits=False, min_digits=10, length=8)
    pw = generate_password(opts)
    assert not any(c.isdigit() for c in pw)


def test_password_length_clamped():
    assert len(generate_password(PasswordOptions(length=99999))) == \
        password_gen.LENGTH_MAX
    assert len(generate_password(PasswordOptions(length=-5))) == \
        password_gen.LENGTH_MIN


# ─── Парольная фраза ─────────────────────────────────────────────────────────

def test_passphrase_words_and_separator():
    ph = generate_passphrase(PassphraseOptions(
        words=4, separator="-", capitalize=False, add_digit=False))
    parts = ph.split("-")
    assert len(parts) == 4
    assert all(p in password_gen.WORDLIST for p in parts)


def test_passphrase_capitalize_and_digit():
    ph = generate_passphrase(PassphraseOptions(
        words=3, separator=".", capitalize=True, add_digit=True))
    parts = ph.split(".")
    assert all(p[0].isupper() for p in parts)
    assert sum(c.isdigit() for c in ph) == 1


def test_passphrase_empty_separator():
    ph = generate_passphrase(PassphraseOptions(
        words=3, separator="", capitalize=True, add_digit=False))
    assert ph and "-" not in ph


def test_wordlist_sane():
    assert len(password_gen.WORDLIST) >= 256          # энтропия ≥ 8 бит/слово
    assert len(set(password_gen.WORDLIST)) == len(password_gen.WORDLIST)
    assert all(w.isalpha() and w.islower() for w in password_gen.WORDLIST)


# ─── Сериализация настроек ───────────────────────────────────────────────────

def test_settings_roundtrip():
    s = GeneratorSettings(
        mode=password_gen.MODE_PHRASE,
        password=PasswordOptions(length=24, use_upper=False, min_digits=3),
        phrase=PassphraseOptions(words=7, separator="_", capitalize=False))
    assert settings_from_config(settings_to_config(s)) == s


@pytest.mark.parametrize("raw", [None, [], "мусор", 42])
def test_settings_from_garbage_value(raw):
    assert settings_from_config(raw) == GeneratorSettings()


def test_settings_from_garbage_fields():
    raw = {"mode": "hack", "pw_length": "abc", "pw_upper": "yes",
           "ph_words": 9999, "ph_separator": ["-"]}
    s = settings_from_config(raw)
    assert s.mode == password_gen.MODE_PASSWORD
    assert s.password.length == 16
    assert s.password.use_upper is True
    assert s.phrase.words == password_gen.WORDS_MAX
    assert s.phrase.separator == "-"


def test_generate_from_config_defaults():
    class FakeConfig:
        def get(self, key, default=None):
            return {}
    assert len(password_gen.generate_from_config(FakeConfig())) == 16


# ─── Диалог настроек ─────────────────────────────────────────────────────────

@pytest.fixture
def dialog(qapp, pure_config):
    from ui_generator import GeneratorSettingsDialog
    d = GeneratorSettingsDialog(pure_config)
    yield d
    d.deleteLater()


def test_dialog_preview_filled(dialog):
    assert len(dialog._preview.text()) == 16


def test_dialog_collect_roundtrip(dialog):
    dialog._pw_length.setValue(20)
    dialog._pw_similar.setChecked(True)
    s = dialog.collect_settings()
    assert s.password.length == 20
    assert s.password.exclude_similar is True
    assert s.mode == password_gen.MODE_PASSWORD


def test_dialog_mode_follows_tab(dialog):
    dialog._tabs.setCurrentIndex(1)
    assert dialog.collect_settings().mode == password_gen.MODE_PHRASE


def test_dialog_cannot_uncheck_all_charsets(dialog):
    for box in (dialog._pw_upper, dialog._pw_lower, dialog._pw_digits):
        box.setChecked(False)
    dialog._pw_symbols.setChecked(False)          # последний — не даст снять
    assert dialog._pw_symbols.isChecked()
    assert dialog._preview.text()                 # предпросмотр жив


def test_dialog_preview_text_for_insertion(dialog):
    # preview_text() — что подставляется в поле пароля карточки при «Сохранить».
    assert dialog.preview_text() == dialog._preview.text() != ""


def test_dialog_min_fields_dimmed_with_checkbox(dialog):
    assert dialog._pw_min_digits.isEnabled()
    dialog._pw_digits.setChecked(False)
    assert not dialog._pw_min_digits.isEnabled()
    dialog._pw_digits.setChecked(True)
    assert dialog._pw_min_digits.isEnabled()


def test_dialog_accept_writes_config(dialog, pure_config):
    dialog._pw_length.setValue(32)
    dialog._tabs.setCurrentIndex(1)
    dialog.accept()
    raw = pure_config.get(password_gen.CONFIG_KEY)
    assert raw["pw_length"] == 32
    assert raw["mode"] == password_gen.MODE_PHRASE
    # Круг: главная кнопка генерации использует сохранённое.
    s = settings_from_config(raw)
    assert s.mode == password_gen.MODE_PHRASE
