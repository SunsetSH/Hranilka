"""Генерация паролей и парольных фраз по настраиваемым правилам.

Qt-free доменный модуль: настройки хранятся в config.json под ключом
CONFIG_KEY (словарь), читаются с защитной валидацией (файл редактируется
пользователем — мусор приводится к дефолтам/диапазонам). Вся случайность —
на secrets (криптостойко). UI настроек — в ui_generator.py.
"""
from __future__ import annotations

import math
import secrets
from dataclasses import dataclass, field
from typing import Any, Mapping

from hranilka.generators.wordlist_data import WORDLIST

CONFIG_KEY = "password_gen"

MODE_PASSWORD = "password"
MODE_PHRASE = "phrase"

LENGTH_MIN, LENGTH_MAX = 5, 128
WORDS_MIN, WORDS_MAX = 3, 12
MIN_COUNT_MAX = 10
SEPARATOR_MAX_LEN = 3
_POLICY_RETRIES = 100

_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LOWER = "abcdefghijklmnopqrstuvwxyz"
_DIGITS = "0123456789"
_SYMBOLS = "!@#$%^&*"
# Похожие/трудноразличимые символы (опция «исключать похожие»).
_SIMILAR = set("0O1lI")

# Словарь парольных фраз — 2048 слов (11 бит/слово), см. wordlist_data.py.
WORDS_DEFAULT = 6
DIGIT_BITS = math.log2(10)

@dataclass(frozen=True)
class PasswordOptions:
    """Опции генерации пароля из случайных символов."""
    length: int = 16
    use_upper: bool = True
    use_lower: bool = True
    use_digits: bool = True
    use_symbols: bool = True
    min_digits: int = 1
    min_symbols: int = 1
    exclude_similar: bool = False


@dataclass(frozen=True)
class PassphraseOptions:
    """Опции генерации парольной фразы из словарных слов."""
    words: int = WORDS_DEFAULT
    separator: str = "-"
    capitalize: bool = True
    add_digit: bool = True


@dataclass(frozen=True)
class GeneratorSettings:
    """Полные настройки генератора: режим + опции обоих режимов."""
    mode: str = MODE_PASSWORD
    password: PasswordOptions = field(default_factory=PasswordOptions)
    phrase: PassphraseOptions = field(default_factory=PassphraseOptions)


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _secure_shuffle(items: list[str]) -> None:
    """Перемешивание Фишера–Йетса на secrets (random.shuffle не криптостоек)."""
    for i in range(len(items) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        items[i], items[j] = items[j], items[i]


def generate_password(opts: PasswordOptions) -> str:
    """Криптостойкий пароль по опциям.

    Минимумы цифр/спецсимволов и по одному символу каждого включённого класса
    гарантируются конструктивно (сначала обязательные символы, затем добор из
    общего пула, затем перемешивание) + проверка политики с повтором.
    Минимумы, не влезающие в длину, срезаются; выключенный набор обнуляет
    свой минимум. Ни один набор не включён — ValueError (UI не допускает)."""
    length = _clamp(opts.length, LENGTH_MIN, LENGTH_MAX)

    def filtered(chars: str) -> str:
        if not opts.exclude_similar:
            return chars
        return "".join(c for c in chars if c not in _SIMILAR)

    digits = filtered(_DIGITS) if opts.use_digits else ""
    symbols = _SYMBOLS if opts.use_symbols else ""
    pool = (filtered(_UPPER) if opts.use_upper else "") + \
           (filtered(_LOWER) if opts.use_lower else "") + digits + symbols
    if not pool:
        raise ValueError("Не выбран ни один набор символов")

    min_digits = _clamp(opts.min_digits, 0, MIN_COUNT_MAX) if digits else 0
    min_symbols = _clamp(opts.min_symbols, 0, MIN_COUNT_MAX) if symbols else 0
    overflow = min_digits + min_symbols - length
    if overflow > 0:                       # срезаем лишнее (сначала спецсимволы)
        cut = min(overflow, min_symbols)
        min_symbols -= cut
        min_digits -= overflow - cut

    classes = [c for c in (
        filtered(_UPPER) if opts.use_upper else "",
        filtered(_LOWER) if opts.use_lower else "",
        digits, symbols) if c]
    for _ in range(_POLICY_RETRIES):
        chars = [secrets.choice(digits) for _ in range(min_digits)]
        chars += [secrets.choice(symbols) for _ in range(min_symbols)]
        # По одному символу каждого включённого класса (если влезает в длину).
        for cls in classes:
            if len(chars) >= length:
                break
            if not any(ch in cls for ch in chars):
                chars.append(secrets.choice(cls))
        chars += [secrets.choice(pool) for _ in range(length - len(chars))]
        _secure_shuffle(chars)
        if _policy_met(chars, classes, min_digits, min_symbols, digits, symbols):
            break
    return "".join(chars)


def _policy_met(chars: list[str], classes: list[str], min_digits: int,
                min_symbols: int, digits: str, symbols: str) -> bool:
    """Политика: минимумы цифр/спецсимволов и по символу каждого класса
    (последнее — только пока классов не больше длины)."""
    if sum(ch in digits for ch in chars) < min_digits:
        return False
    if sum(ch in symbols for ch in chars) < min_symbols:
        return False
    if len(classes) > len(chars):
        return True
    return all(any(ch in cls for ch in chars) for cls in classes)


def passphrase_entropy_bits(opts: PassphraseOptions) -> float:
    """Энтропия парольной фразы в битах по настройкам (с учётом clamp слов).

    log2(N) на слово; цифра в конце одного из слов добавляет log2(слов) на
    выбор слова и log2(10) на саму цифру. Заглавные и разделитель
    детерминированы и энтропии не добавляют."""
    count = _clamp(opts.words, WORDS_MIN, WORDS_MAX)
    bits = count * math.log2(len(WORDLIST))
    if opts.add_digit:
        bits += math.log2(count) + DIGIT_BITS
    return bits


def generate_passphrase(opts: PassphraseOptions) -> str:
    """Парольная фраза из случайных словарных слов."""
    count = _clamp(opts.words, WORDS_MIN, WORDS_MAX)
    words = [secrets.choice(WORDLIST) for _ in range(count)]
    if opts.capitalize:
        words = [w.capitalize() for w in words]
    if opts.add_digit:
        i = secrets.randbelow(count)
        words[i] += str(secrets.randbelow(10))
    return opts.separator[:SEPARATOR_MAX_LEN].join(words)


def generate(settings: GeneratorSettings) -> str:
    """Сгенерировать секрет по активному режиму настроек."""
    if settings.mode == MODE_PHRASE:
        return generate_passphrase(settings.phrase)
    return generate_password(settings.password)


# ─── Сериализация настроек (словарь в config.json) ───────────────────────────

def _get_bool(raw: Mapping[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    return value if isinstance(value, bool) else default


def _get_int(raw: Mapping[str, Any], key: str, default: int,
             lo: int, hi: int) -> int:
    try:
        return _clamp(int(raw.get(key, default)), lo, hi)
    except (TypeError, ValueError):
        return default


def settings_from_config(raw: Any) -> GeneratorSettings:
    """Настройки из значения config.json: любой мусор → дефолты/диапазоны."""
    if not isinstance(raw, Mapping):
        return GeneratorSettings()
    mode = raw.get("mode")
    if mode not in (MODE_PASSWORD, MODE_PHRASE):
        mode = MODE_PASSWORD
    separator = raw.get("ph_separator", "-")
    if not isinstance(separator, str):
        separator = "-"
    return GeneratorSettings(
        mode=mode,
        password=PasswordOptions(
            length=_get_int(raw, "pw_length", 16, LENGTH_MIN, LENGTH_MAX),
            use_upper=_get_bool(raw, "pw_upper", True),
            use_lower=_get_bool(raw, "pw_lower", True),
            use_digits=_get_bool(raw, "pw_digits", True),
            use_symbols=_get_bool(raw, "pw_symbols", True),
            min_digits=_get_int(raw, "pw_min_digits", 1, 0, MIN_COUNT_MAX),
            min_symbols=_get_int(raw, "pw_min_symbols", 1, 0, MIN_COUNT_MAX),
            exclude_similar=_get_bool(raw, "pw_exclude_similar", False),
        ),
        phrase=PassphraseOptions(
            words=_get_int(raw, "ph_words", WORDS_DEFAULT, WORDS_MIN, WORDS_MAX),
            separator=separator[:SEPARATOR_MAX_LEN],
            capitalize=_get_bool(raw, "ph_capitalize", True),
            add_digit=_get_bool(raw, "ph_digit", True),
        ),
    )


def settings_to_config(settings: GeneratorSettings) -> dict[str, Any]:
    """Настройки → словарь для config.json (обратное к settings_from_config)."""
    pw, ph = settings.password, settings.phrase
    return {
        "mode": settings.mode,
        "pw_length": pw.length,
        "pw_upper": pw.use_upper,
        "pw_lower": pw.use_lower,
        "pw_digits": pw.use_digits,
        "pw_symbols": pw.use_symbols,
        "pw_min_digits": pw.min_digits,
        "pw_min_symbols": pw.min_symbols,
        "pw_exclude_similar": pw.exclude_similar,
        "ph_words": ph.words,
        "ph_separator": ph.separator,
        "ph_capitalize": ph.capitalize,
        "ph_digit": ph.add_digit,
    }


def generate_from_config(config: Any) -> str:
    """Секрет по настройкам из Config программы (или словаря с .get)."""
    return generate(settings_from_config(config.get(CONFIG_KEY, {}) or {}))
