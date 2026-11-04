"""Аудит B12: по символу каждого включённого класса + политика."""
import itertools

from hranilka.generators import password_gen
from hranilka.generators.password_gen import PasswordOptions, generate_password

_CLASSES = {
    "upper": password_gen._UPPER, "lower": password_gen._LOWER,
    "digits": password_gen._DIGITS, "symbols": password_gen._SYMBOLS,
}


def _check(pw, opts):
    for name, chars in _CLASSES.items():
        if getattr(opts, "use_" + name):
            assert any(c in chars for c in pw), (name, pw)
        else:
            assert not any(c in chars for c in pw), (name, pw)


def test_each_enabled_class_present():
    for up, lo, di, sy in itertools.product([False, True], repeat=4):
        if not (up or lo or di or sy):
            continue
        opts = PasswordOptions(length=8, use_upper=up, use_lower=lo,
                               use_digits=di, use_symbols=sy,
                               min_digits=0, min_symbols=0)
        for _ in range(100):
            _check(generate_password(opts), opts)


def test_short_length_all_classes():
    opts = PasswordOptions(length=5, min_digits=1, min_symbols=1)
    for _ in range(300):
        pw = generate_password(opts)
        assert len(pw) == 5
        _check(pw, opts)


def test_exclude_similar_still_has_classes():
    opts = PasswordOptions(length=6, exclude_similar=True,
                           min_digits=0, min_symbols=0)
    for _ in range(300):
        pw = generate_password(opts)
        assert not set(pw) & set("0O1lI")
        _check(pw, opts)


def test_min_counts_respected():
    opts = PasswordOptions(length=12, min_digits=3, min_symbols=2)
    for _ in range(200):
        pw = generate_password(opts)
        assert sum(c in password_gen._DIGITS for c in pw) >= 3
        assert sum(c in password_gen._SYMBOLS for c in pw) >= 2
