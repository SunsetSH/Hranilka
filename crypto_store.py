"""Шифрование всего документа «Хранилки» мастер-паролем (опционально).

Схема — конвертное (envelope) шифрование:

    DEK (32 случайных байта) ──AES-256-GCM──> сериализованная БД
      ├─ wrap_pw  = AES-GCM( Argon2id(пароль,   salt_pw),  DEK )
      └─ wrap_rec = AES-GCM( Argon2id(recovery,  salt_rec), DEK )

DEK напрямую шифрует данные; сам DEK хранится дважды — обёрнутым паролем и
recovery-кодом. За счёт этого смена пароля = перезаворачивание DEK (быстро,
без перешифровки всей БД), а recovery-код даёт независимый доступ.

Файл-контейнер на диске (бинарный):

    MAGIC(6) | VER(1) | preset(1) | t(1) m_kib(4) p(1)
    | salt_pw(16) | wrap_pw_len(2) | wrap_pw(nonce12+ct+tag16)
    | salt_rec(16) | wrap_rec_len(2) | wrap_rec(nonce12+ct+tag16)
    | data_nonce(12) | data_ct (+tag16, до конца файла)

Пароль/recovery/ключи нигде, кроме контейнера, не хранятся."""

import os
import struct
import secrets

from argon2.low_level import hash_secret_raw, Type
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"HRNKv1"
VERSION = 1

NONCE_LEN = 12
SALT_LEN = 16
DEK_LEN = 32  # AES-256

# Пресеты Argon2id: (имя -> (time_cost, memory_kib, parallelism)).
PRESETS = {
    "fast":     (2, 32 * 1024, 2),   # Быстро    (~32 МБ)
    "balanced": (3, 64 * 1024, 4),   # Сбалансировано (~64 МБ)
    "paranoid": (4, 256 * 1024, 4),  # Параноик  (~256 МБ)
}
DEFAULT_PRESET = "balanced"
_PRESET_IDS = {"fast": 0, "balanced": 1, "paranoid": 2}
_ID_PRESETS = {v: k for k, v in _PRESET_IDS.items()}


class WrongPassword(Exception):
    """Неверный пароль/recovery-код или повреждённый контейнер."""


class CorruptVault(Exception):
    """Файл не является контейнером «Хранилки» или испорчен заголовок."""


def _derive_key(secret: str, salt: bytes, params) -> bytes:
    """Argon2id → 32-байтный ключ для AES-256."""
    t, m_kib, p = params
    return hash_secret_raw(
        secret=secret.encode("utf-8"),
        salt=salt,
        time_cost=t,
        memory_cost=m_kib,
        parallelism=p,
        hash_len=DEK_LEN,
        type=Type.ID,
    )


def _wrap(dek: bytes, secret: str, salt: bytes, params) -> bytes:
    """Зашифровать DEK ключом, выведенным из secret. Возвращает nonce+ct+tag."""
    key = _derive_key(secret, salt, params)
    nonce = os.urandom(NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, dek, None)
    return nonce + ct


def _unwrap(blob: bytes, secret: str, salt: bytes, params) -> bytes:
    """Расшифровать DEK. WrongPassword при неверном secret (провал тега GCM)."""
    key = _derive_key(secret, salt, params)
    nonce, ct = blob[:NONCE_LEN], blob[NONCE_LEN:]
    try:
        return AESGCM(key).decrypt(nonce, ct, None)
    except Exception:
        raise WrongPassword()


def generate_recovery_code() -> str:
    """Код восстановления высокой энтропии: 32 символа base32, группами по 4."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"  # без 0/1/8/9 для читаемости
    raw = "".join(secrets.choice(alphabet) for _ in range(32))
    return "-".join(raw[i:i + 4] for i in range(0, 32, 4))


def normalize_recovery_code(code: str) -> str:
    """Привести введённый recovery-код к каноничному виду (для сравнения)."""
    cleaned = "".join(ch for ch in code.upper() if ch.isalnum())
    return "-".join(cleaned[i:i + 4] for i in range(0, len(cleaned), 4))


def _params_for(preset: str):
    return PRESETS.get(preset, PRESETS[DEFAULT_PRESET])


def seal(db_bytes: bytes, dek: bytes, header: dict) -> bytes:
    """Собрать контейнер из уже известного DEK и заголовка (с обёрнутыми DEK).

    header содержит: preset, params, salt_pw, wrap_pw, salt_rec, wrap_rec."""
    t, m_kib, p = header["params"]
    data_nonce = os.urandom(NONCE_LEN)
    data_ct = AESGCM(dek).encrypt(data_nonce, db_bytes, None)

    out = bytearray()
    out += MAGIC
    out += struct.pack("<B", VERSION)
    out += struct.pack("<B", _PRESET_IDS.get(header["preset"], 1))
    out += struct.pack("<B", t)
    out += struct.pack("<I", m_kib)
    out += struct.pack("<B", p)
    out += header["salt_pw"]
    out += struct.pack("<H", len(header["wrap_pw"]))
    out += header["wrap_pw"]
    out += header["salt_rec"]
    out += struct.pack("<H", len(header["wrap_rec"]))
    out += header["wrap_rec"]
    out += data_nonce
    out += data_ct
    return bytes(out)


def _parse(container: bytes):
    """Разобрать контейнер → (header_dict, data_nonce, data_ct)."""
    if container[:len(MAGIC)] != MAGIC:
        raise CorruptVault("Неверная сигнатура файла")
    off = len(MAGIC)
    try:
        version = container[off]; off += 1
        if version != VERSION:
            raise CorruptVault(f"Неподдерживаемая версия контейнера: {version}")
        preset_id = container[off]; off += 1
        t = container[off]; off += 1
        (m_kib,) = struct.unpack_from("<I", container, off); off += 4
        p = container[off]; off += 1
        salt_pw = container[off:off + SALT_LEN]; off += SALT_LEN
        (wpl,) = struct.unpack_from("<H", container, off); off += 2
        wrap_pw = container[off:off + wpl]; off += wpl
        salt_rec = container[off:off + SALT_LEN]; off += SALT_LEN
        (wrl,) = struct.unpack_from("<H", container, off); off += 2
        wrap_rec = container[off:off + wrl]; off += wrl
        data_nonce = container[off:off + NONCE_LEN]; off += NONCE_LEN
        data_ct = container[off:]
    except (IndexError, struct.error):
        raise CorruptVault("Повреждён заголовок контейнера")

    # Заголовок — НЕДОВЕРЕННЫЙ вход. Жёстко валидируем всё ДО запуска Argon2:
    # иначе подделанный файл может затребовать гигантскую память/число проходов
    # (t, m_kib, p читаются прямо из файла) и повесить/уронить процесс при
    # разблокировке ещё до проверки тега GCM. Принимаем только параметры
    # известных пресетов и точные длины полей.
    if (t, m_kib, p) not in set(PRESETS.values()):
        raise CorruptVault("Недопустимые параметры KDF в заголовке")
    _WRAP_LEN = NONCE_LEN + DEK_LEN + 16  # nonce + обёрнутый DEK + тег GCM
    if len(salt_pw) != SALT_LEN or len(salt_rec) != SALT_LEN:
        raise CorruptVault("Повреждён заголовок контейнера")
    if len(wrap_pw) != _WRAP_LEN or len(wrap_rec) != _WRAP_LEN:
        raise CorruptVault("Повреждён заголовок контейнера")
    if len(data_nonce) != NONCE_LEN or len(data_ct) < 16:
        raise CorruptVault("Повреждён контейнер")

    header = {
        "preset": _ID_PRESETS.get(preset_id, DEFAULT_PRESET),
        "params": (t, m_kib, p),
        "salt_pw": salt_pw,
        "wrap_pw": wrap_pw,
        "salt_rec": salt_rec,
        "wrap_rec": wrap_rec,
    }
    return header, data_nonce, data_ct


def create_vault(db_bytes: bytes, password: str, preset: str = DEFAULT_PRESET):
    """Зашифровать БД новым паролем. Возвращает (container_bytes, recovery_code).

    Recovery-код генерируется здесь и больше нигде не сохраняется — его должен
    записать пользователь."""
    params = _params_for(preset)
    recovery = generate_recovery_code()
    dek = os.urandom(DEK_LEN)
    salt_pw = os.urandom(SALT_LEN)
    salt_rec = os.urandom(SALT_LEN)
    header = {
        "preset": preset,
        "params": params,
        "salt_pw": salt_pw,
        "wrap_pw": _wrap(dek, password, salt_pw, params),
        "salt_rec": salt_rec,
        "wrap_rec": _wrap(dek, recovery, salt_rec, params),
    }
    container = seal(db_bytes, dek, header)
    return container, recovery


def unlock(container: bytes, secret: str, is_recovery: bool = False):
    """Расшифровать контейнер. Возвращает (db_bytes, dek, header).

    db_bytes — сериализованная БД для sqlite deserialize(); dek и header нужны,
    чтобы потом пересобрать контейнер при сохранении без повторного ввода."""
    header, data_nonce, data_ct = _parse(container)
    if is_recovery:
        secret = normalize_recovery_code(secret)
        dek = _unwrap(header["wrap_rec"], secret, header["salt_rec"], header["params"])
    else:
        dek = _unwrap(header["wrap_pw"], secret, header["salt_pw"], header["params"])
    try:
        db_bytes = AESGCM(dek).decrypt(data_nonce, data_ct, None)
    except Exception:
        raise WrongPassword()
    return db_bytes, dek, header


def change_password(container: bytes, dek: bytes, header: dict,
                    new_password: str, preset: str = None):
    """Перезавернуть DEK новым паролем (данные не перешифровываются повторно
    в смысле KDF: меняется только wrap_pw). Возвращает новый header.

    preset=None — сохранить текущий; иначе сменить стойкость KDF для пароля."""
    new_header = dict(header)
    if preset is not None:
        new_header["preset"] = preset
        new_header["params"] = _params_for(preset)
    params = new_header["params"]
    salt_pw = os.urandom(SALT_LEN)
    new_header["salt_pw"] = salt_pw
    new_header["wrap_pw"] = _wrap(dek, new_password, salt_pw, params)
    return new_header


def regenerate_recovery(dek: bytes, header: dict):
    """Сгенерировать новый recovery-код и перезавернуть им DEK.
    Возвращает (new_header, recovery_code)."""
    new_header = dict(header)
    recovery = generate_recovery_code()
    params = new_header["params"]
    salt_rec = os.urandom(SALT_LEN)
    new_header["salt_rec"] = salt_rec
    new_header["wrap_rec"] = _wrap(dek, recovery, salt_rec, params)
    return new_header, recovery


def is_encrypted_file(path: str) -> bool:
    """Быстрая проверка по сигнатуре, зашифрован ли файл-БД."""
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False
