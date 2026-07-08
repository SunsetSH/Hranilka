"""Генерация паролей и парольных фраз по настраиваемым правилам.

Qt-free доменный модуль: настройки хранятся в config.json под ключом
CONFIG_KEY (словарь), читаются с защитной валидацией (файл редактируется
пользователем — мусор приводится к дефолтам/диапазонам). Вся случайность —
на secrets (криптостойко). UI настроек — в ui_generator.py.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any, Mapping

CONFIG_KEY = "password_gen"

MODE_PASSWORD = "password"
MODE_PHRASE = "phrase"

LENGTH_MIN, LENGTH_MAX = 5, 128
WORDS_MIN, WORDS_MAX = 3, 12
MIN_COUNT_MAX = 10
SEPARATOR_MAX_LEN = 3

_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LOWER = "abcdefghijklmnopqrstuvwxyz"
_DIGITS = "0123456789"
_SYMBOLS = "!@#$%^&*"
# Похожие/трудноразличимые символы (опция «исключать похожие»).
_SIMILAR = set("0O1lI")

# Словарь парольных фраз: короткие частотные английские слова (3–7 букв).
# ~380 слов ≈ 8.5 бит/слово; 5 слов ≈ 43 бита + разделители/цифра.
WORDLIST: tuple[str, ...] = (
    "acid", "actor", "alarm", "album", "alien", "alley", "amber", "angle",
    "ankle", "apple", "april", "arena", "argue", "armor", "arrow", "atlas",
    "attic", "auto", "axis", "bacon", "badge", "baker", "bamboo", "banjo",
    "barn", "basil", "beach", "beacon", "bean", "bear", "beard", "beetle",
    "bell", "belt", "bench", "berry", "bird", "bison", "blade", "blanket",
    "blast", "blaze", "block", "bloom", "blue", "board", "boat", "bolt",
    "bonus", "book", "boot", "born", "bottle", "bounce", "brain", "brave",
    "bread", "breeze", "brick", "bridge", "brook", "broom", "brush", "bubble",
    "bucket", "buddy", "budget", "buffalo", "bugle", "bunny", "burst", "butter",
    "cabin", "cable", "cactus", "cake", "camel", "camera", "candle", "candy",
    "canoe", "canvas", "canyon", "carbon", "cargo", "carpet", "carrot", "castle",
    "cattle", "cedar", "cellar", "cement", "chain", "chair", "chalk", "charm",
    "cheese", "cherry", "chess", "chest", "chief", "chill", "choir", "circle",
    "citrus", "city", "claw", "clay", "cliff", "climb", "clock", "cloud",
    "clover", "coach", "coast", "cobalt", "cocoa", "coffee", "coin", "comet",
    "compass", "coral", "corn", "cotton", "cougar", "county", "cover", "coyote",
    "crab", "craft", "crane", "crater", "crayon", "creek", "cricket", "crown",
    "cruise", "crystal", "cube", "curtain", "cycle", "daisy", "dance", "dawn",
    "decade", "deer", "delta", "denim", "desert", "desk", "dial", "diamond",
    "diesel", "dinner", "dolphin", "dome", "donkey", "door", "dragon", "drift",
    "drum", "dune", "dust", "eagle", "earth", "echo", "eight", "elbow",
    "elder", "elm", "ember", "emerald", "engine", "envoy", "estate", "fabric",
    "falcon", "fancy", "farm", "feather", "fence", "fern", "ferry", "fiber",
    "field", "finch", "flag", "flame", "flash", "fleet", "flint", "flood",
    "floor", "flour", "flute", "fog", "forest", "fork", "fossil", "fountain",
    "fox", "frame", "friend", "frost", "fruit", "galaxy", "garden", "garlic",
    "gate", "gecko", "gentle", "giant", "ginger", "glacier", "glass", "globe",
    "glove", "gold", "goose", "gorge", "grain", "granite", "grape", "grass",
    "gravel", "green", "grill", "grove", "guitar", "gulf", "hammer", "harbor",
    "harvest", "hawk", "hazel", "heart", "hedge", "helmet", "herb", "heron",
    "hill", "hive", "hobby", "hockey", "honey", "hood", "hook", "horizon",
    "horse", "hotel", "house", "hunter", "ice", "igloo", "index", "iron",
    "island", "ivory", "jacket", "jade", "jaguar", "jelly", "jewel", "jungle",
    "juice", "kayak", "kettle", "kiwi", "knight", "koala", "ladder", "lagoon",
    "lake", "lamp", "lantern", "laser", "laurel", "lava", "leaf", "ledge",
    "lemon", "lentil", "level", "lily", "lime", "linen", "lion", "lizard",
    "lobby", "locket", "lodge", "lotus", "lumber", "lunar", "lynx", "magnet",
    "mango", "mantle", "maple", "marble", "market", "mask", "meadow", "medal",
    "melon", "mesa", "metal", "meteor", "mint", "mirror", "mole", "monitor",
    "moon", "morning", "mosaic", "moss", "moth", "motor", "mountain", "mouse",
    "mural", "music", "napkin", "nectar", "needle", "nickel", "night", "noble",
    "north", "nova", "nugget", "nutmeg", "oak", "oasis", "ocean", "olive",
    "onion", "opal", "orange", "orbit", "orchid", "otter", "owl", "oxygen",
    "oyster", "paddle", "palace", "palm", "panda", "panel", "paper", "parade",
    "parrot", "pasta", "patio", "peach", "pearl", "pebble", "pencil", "penguin",
    "pepper", "petal", "photo", "piano", "pickle", "pigeon", "pillow", "pilot",
    "pine", "pistol", "pixel", "pizza", "planet", "plank", "plaza", "plum",
    "pocket", "polar", "pond", "poppy", "portal", "prairie", "prism", "pump",
    "pumpkin", "puzzle", "pyramid", "quartz", "quill", "rabbit", "raccoon",
    "radar", "radio", "raft", "rain", "ranch", "raven", "reef", "ribbon",
    "rice", "ridge", "rifle", "river", "roast", "robin", "rocket", "roof",
    "rope", "rose", "ruby", "rustic", "saddle", "sage", "salad", "salmon",
    "salt", "sand", "sapphire", "satin", "sauce", "scale", "scarf", "school",
    "scout", "sea", "seed", "shadow", "shark", "shelf", "shell", "shield",
    "shore", "silk", "silver", "sketch", "skill", "sky", "slate", "sleigh",
    "smile", "snow", "socket", "solar", "sonar", "spark", "sphere", "spice",
    "spider", "spiral", "spring", "spruce", "square", "stable", "stadium",
    "star", "steam", "steel", "stone", "storm", "stove", "straw", "stream",
    "street", "sugar", "summit", "sun", "sunset", "swan", "sweet", "syrup",
    "table", "talon", "tango", "tea", "temple", "tent", "thunder", "tiger",
    "timber", "toast", "token", "tomato", "torch", "tower", "trail", "train",
    "tree", "tribe", "trout", "trumpet", "tulip", "tunnel", "turtle", "urban",
    "valley", "vanilla", "vapor", "velvet", "verse", "vessel", "vine", "violet",
    "violin", "vista", "volcano", "wagon", "walnut", "walrus", "water", "wave",
    "weather", "whale", "wheat", "wheel", "willow", "wind", "window", "winter",
    "wolf", "wonder", "wood", "wool", "yarn", "yellow", "zebra", "zenith",
)


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
    words: int = 5
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

    Минимумы цифр/спецсимволов гарантируются конструктивно (сначала кладём
    обязательные символы, затем добор из общего пула, затем перемешивание).
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

    chars = [secrets.choice(digits) for _ in range(min_digits)]
    chars += [secrets.choice(symbols) for _ in range(min_symbols)]
    chars += [secrets.choice(pool) for _ in range(length - len(chars))]
    _secure_shuffle(chars)
    return "".join(chars)


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
            words=_get_int(raw, "ph_words", 5, WORDS_MIN, WORDS_MAX),
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
