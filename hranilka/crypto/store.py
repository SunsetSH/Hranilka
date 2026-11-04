"""Шифрование всего документа «Хранилки» мастер-паролем (опционально).

Схема — конвертное (envelope) шифрование:

    DEK (32 случайных байта) ──AES-256-GCM──> сериализованная БД
      ├─ wrap_pw  = AES-GCM( Argon2id(пароль,   salt_pw),  DEK )
      └─ wrap_rec = AES-GCM( Argon2id(recovery,  salt_rec), DEK )

DEK напрямую шифрует данные; сам DEK хранится дважды — обёрнутым паролем и
recovery-кодом. За счёт этого смена пароля = перезаворачивание DEK (быстро,
без перешифровки всей БД), а recovery-код даёт независимый доступ.

Файл-контейнер на диске (бинарный), версия 2:

    MAGIC(6) | VER(1)=2 | flags(1) | preset(1) | t(1) m_kib(4) p(1)
    | salt_pw(16) | wrap_pw_len(2) | wrap_pw(nonce12+ct+tag16)
    | salt_rec(16) | wrap_rec_len(2) | wrap_rec(nonce12+ct+tag16)
    | data_nonce(12) | data_ct (+tag16, до конца файла)

flags: bit0 - пароль нормализуется в NFC (S3); bit1 - wrap_rec выведен через
HKDF, а не Argon2 (S9). Данные шифруются с AAD = все байты заголовка до
data_nonce (S1): подмена пресета/солей/обёрток ломает GCM-тег.
Версия 1 (без flags и AAD) читается как есть; первое сохранение переписывает
её в v2 с flags сохранённых кодировок (обёртки не перешифровываются).

Пароль/recovery/ключи нигде, кроме контейнера, не хранятся."""

import os
import struct
import secrets
import unicodedata

from argon2.low_level import hash_secret_raw, Type
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"HRNKv1"
VERSION = 2            # версия, в которой пишем
_READ_VERSIONS = (1, 2)
FLAG_PW_NFC = 1        # пароль в NFC
FLAG_REC_HKDF = 2      # wrap_rec через HKDF
_HKDF_INFO = b"hranilka-recovery-wrap-v1"

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


def _derive_key(secret: str, salt: bytes, params, nfc: bool = True) -> bytes:
    """Argon2id → 32-байтный ключ для AES-256.

    nfc=True — пароль нормализуется в Unicode NFC (S3: одна и та же строка с
    разной раскладкой/ОС даёт одни байты). nfc=False — старая кодировка
    контейнеров v1 (сырой UTF-8)."""
    t, m_kib, p = params
    if nfc:
        secret = unicodedata.normalize("NFC", secret)
    return hash_secret_raw(
        secret=secret.encode("utf-8"),
        salt=salt,
        time_cost=t,
        memory_cost=m_kib,
        parallelism=p,
        hash_len=DEK_LEN,
        type=Type.ID,
    )


def _derive_recovery_key(code: str, salt: bytes) -> bytes:
    """Ключ обёртки recovery-кода через HKDF (S9): код уже имеет 160 бит
    энтропии, Argon2 на нём — лишняя работа."""
    return HKDF(algorithm=hashes.SHA256(), length=DEK_LEN, salt=salt,
                info=_HKDF_INFO).derive(code.encode("utf-8"))


def _wrap_with_key(dek: bytes, key: bytes) -> bytes:
    nonce = os.urandom(NONCE_LEN)
    return nonce + AESGCM(key).encrypt(nonce, dek, None)


def _unwrap_with_key(blob: bytes, key: bytes) -> bytes:
    nonce, ct = blob[:NONCE_LEN], blob[NONCE_LEN:]
    try:
        return AESGCM(key).decrypt(nonce, ct, None)
    except InvalidTag:
        # Только провал аутентификации GCM = неверный secret. Прочие исключения
        # (MemoryError, прерывание) НЕ маскируем под «неверный пароль».
        raise WrongPassword()


def _wrap(dek: bytes, secret: str, salt: bytes, params, nfc: bool = True) -> bytes:
    """Зашифровать DEK ключом, выведенным из secret. Возвращает nonce+ct+tag."""
    return _wrap_with_key(dek, _derive_key(secret, salt, params, nfc))


def _unwrap(blob: bytes, secret: str, salt: bytes, params, nfc: bool = True) -> bytes:
    """Расшифровать DEK. WrongPassword при неверном secret (провал тега GCM)."""
    return _unwrap_with_key(blob, _derive_key(secret, salt, params, nfc))


def _unwrap_password(header: dict, password: str) -> bytes:
    """Развернуть DEK паролем с учётом версии кодировки (S3). Контейнер без
    флага NFC (v1/старый) хранит пароль в сыром UTF-8: пробуем его, а если
    ввод не в NFC — ещё и нормализованный вариант (раскладка/ОС другая)."""
    args = (header["wrap_pw"], password, header["salt_pw"], header["params"])
    if header.get("flags", 0) & FLAG_PW_NFC:
        return _unwrap(*args, nfc=True)
    try:
        return _unwrap(*args, nfc=False)
    except WrongPassword:
        if unicodedata.normalize("NFC", password) == password:
            raise
        return _unwrap(*args, nfc=True)


def _unwrap_recovery(header: dict, code: str) -> bytes:
    """Развернуть DEK recovery-кодом (HKDF или старый Argon2 — по флагу)."""
    code = normalize_recovery_code(code)
    if header.get("flags", 0) & FLAG_REC_HKDF:
        return _unwrap_with_key(header["wrap_rec"],
                                _derive_recovery_key(code, header["salt_rec"]))
    return _unwrap(header["wrap_rec"], code, header["salt_rec"],
                   header["params"], nfc=False)


def check_secret(header: dict, secret: str, is_recovery: bool = False) -> bool:
    """Проверить пароль/recovery-код против заголовка (без расшифровки данных)."""
    try:
        if is_recovery:
            _unwrap_recovery(header, secret)
        else:
            _unwrap_password(header, secret)
        return True
    except WrongPassword:
        return False


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


def _header_bytes(header: dict) -> bytes:
    """Байты заголовка контейнера v2 (они же - AAD для секции данных)."""
    t, m_kib, p = header["params"]
    return b"".join((
        MAGIC,
        struct.pack("<BBBBIB", VERSION, header.get("flags", 0),
                    _PRESET_IDS.get(header["preset"], 1), t, m_kib, p),
        header["salt_pw"],
        struct.pack("<H", len(header["wrap_pw"])), header["wrap_pw"],
        header["salt_rec"],
        struct.pack("<H", len(header["wrap_rec"])), header["wrap_rec"],
    ))


def seal_parts(db_bytes: bytes, dek: bytes, header: dict):
    """Как seal(), но возвращает (заголовок, nonce, шифртекст) без склейки -
    писатель может записать части последовательно и не держать лишнюю копию."""
    hdr = _header_bytes(header)
    data_nonce = os.urandom(NONCE_LEN)
    data_ct = AESGCM(bytes(dek)).encrypt(data_nonce, db_bytes, hdr)
    return hdr, data_nonce, data_ct


def seal(db_bytes: bytes, dek: bytes, header: dict) -> bytes:
    """Собрать контейнер из уже известного DEK и заголовка (с обёрнутыми DEK).

    header содержит: preset, params, salt_pw, wrap_pw, salt_rec, wrap_rec и
    (опционально, по умолчанию 0 = старые кодировки) flags."""
    return b"".join(seal_parts(db_bytes, dek, header))


def _parse_full(container):
    """Разобрать контейнер -> (header_dict, data_nonce, data_ct, aad|None).
    aad - байты заголовка для проверки GCM (None для v1)."""
    mv = memoryview(container)
    if bytes(mv[:len(MAGIC)]) != MAGIC:
        raise CorruptVault("Неверная сигнатура файла")
    off = len(MAGIC)
    try:
        version = container[off]; off += 1
        if version not in _READ_VERSIONS:
            raise CorruptVault(f"Неподдерживаемая версия контейнера: {version}")
        flags = 0
        if version >= 2:
            flags = container[off]; off += 1
        preset_id = container[off]; off += 1
        t = container[off]; off += 1
        (m_kib,) = struct.unpack_from("<I", container, off); off += 4
        p = container[off]; off += 1
        salt_pw = bytes(mv[off:off + SALT_LEN]); off += SALT_LEN
        (wpl,) = struct.unpack_from("<H", container, off); off += 2
        wrap_pw = bytes(mv[off:off + wpl]); off += wpl
        salt_rec = bytes(mv[off:off + SALT_LEN]); off += SALT_LEN
        (wrl,) = struct.unpack_from("<H", container, off); off += 2
        wrap_rec = bytes(mv[off:off + wrl]); off += wrl
        aad_end = off
        data_nonce = bytes(mv[off:off + NONCE_LEN]); off += NONCE_LEN
        data_ct = mv[off:]   # без копии (P1)
    except (IndexError, struct.error):
        raise CorruptVault("Повреждён заголовок контейнера")

    # Заголовок - НЕДОВЕРЕННЫЙ вход. Жёстко валидируем всё ДО запуска Argon2:
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
    if flags & ~(FLAG_PW_NFC | FLAG_REC_HKDF):
        raise CorruptVault("Неизвестные флаги контейнера")

    header = {
        "version": version,
        "flags": flags,
        "preset": _ID_PRESETS.get(preset_id, DEFAULT_PRESET),
        "params": (t, m_kib, p),
        "salt_pw": salt_pw,
        "wrap_pw": wrap_pw,
        "salt_rec": salt_rec,
        "wrap_rec": wrap_rec,
    }
    aad = bytes(mv[:aad_end]) if version >= 2 else None
    return header, data_nonce, data_ct, aad


def _parse(container: bytes):
    """Разобрать контейнер -> (header_dict, data_nonce, data_ct)."""
    header, data_nonce, data_ct, _aad = _parse_full(container)
    return header, data_nonce, data_ct


def create_vault(db_bytes: bytes, password: str, preset: str = DEFAULT_PRESET):
    """Зашифровать БД новым паролем. Возвращает (container_bytes, recovery_code).

    Recovery-код генерируется здесь и больше нигде не сохраняется - его должен
    записать пользователь."""
    params = _params_for(preset)
    recovery = generate_recovery_code()
    dek = os.urandom(DEK_LEN)
    salt_pw = os.urandom(SALT_LEN)
    salt_rec = os.urandom(SALT_LEN)
    header = {
        "flags": FLAG_PW_NFC | FLAG_REC_HKDF,
        "preset": preset,
        "params": params,
        "salt_pw": salt_pw,
        "wrap_pw": _wrap(dek, password, salt_pw, params),
        "salt_rec": salt_rec,
        "wrap_rec": _wrap_with_key(dek, _derive_recovery_key(recovery, salt_rec)),
    }
    container = seal(db_bytes, dek, header)
    return container, recovery


def unlock(container: bytes, secret: str, is_recovery: bool = False):
    """Расшифровать контейнер. Возвращает (db_bytes, dek, header).

    db_bytes - сериализованная БД для sqlite deserialize(); dek и header нужны,
    чтобы потом пересобрать контейнер при сохранении без повторного ввода."""
    header, data_nonce, data_ct, aad = _parse_full(container)
    if is_recovery:
        dek = _unwrap_recovery(header, secret)
    else:
        dek = _unwrap_password(header, secret)
    try:
        db_bytes = AESGCM(dek).decrypt(data_nonce, data_ct, aad)
    except InvalidTag:
        # DEK уже успешно развёрнут - секрет верен; провал GCM-тега здесь
        # означает повреждение/подмену секции данных или заголовка, а не
        # неверный пароль.
        raise CorruptVault("повреждена секция данных (GCM-тег не сошёлся)")
    return db_bytes, dek, header


def change_password(container, dek: bytes, header: dict,
                    new_password: str, preset: str | None = None,
                    rotate_dek: bool = False):
    """Перезавернуть DEK новым паролем (данные не перешифровываются повторно
    в смысле KDF: меняется только wrap_pw). Возвращает новый header.

    preset=None - сохранить текущий; иначе сменить стойкость KDF для пароля.

    rotate_dek=True (S2) - ещё и сменить сам DEK: старая копия файла + старый
    пароль больше не расшифруют новые сохранения. Старый recovery-код
    оборачивал старый DEK, поэтому генерируется новый. Возвращает
    (new_header, new_dek, new_recovery_code); данные нужно пересохранить
    (seal) под new_dek - это делает вызывающий."""
    new_header = dict(header)
    if preset is not None:
        new_header["preset"] = preset
        new_header["params"] = _params_for(preset)
    params = new_header["params"]
    salt_pw = os.urandom(SALT_LEN)
    new_header["salt_pw"] = salt_pw
    new_header["flags"] = new_header.get("flags", 0) | FLAG_PW_NFC
    if not rotate_dek:
        new_header["wrap_pw"] = _wrap(dek, new_password, salt_pw, params)
        return new_header
    new_dek = os.urandom(DEK_LEN)
    new_header["wrap_pw"] = _wrap(new_dek, new_password, salt_pw, params)
    new_header, recovery = _rewrap_recovery(new_dek, new_header)
    return new_header, new_dek, recovery


def _rewrap_recovery(dek: bytes, header: dict):
    """Новый recovery-код + wrap_rec (HKDF) для данного DEK. (header, code)."""
    new_header = dict(header)
    recovery = generate_recovery_code()
    salt_rec = os.urandom(SALT_LEN)
    new_header["salt_rec"] = salt_rec
    new_header["wrap_rec"] = _wrap_with_key(
        dek, _derive_recovery_key(recovery, salt_rec))
    new_header["flags"] = new_header.get("flags", 0) | FLAG_REC_HKDF
    return new_header, recovery


def regenerate_recovery(dek: bytes, header: dict):
    """Сгенерировать новый recovery-код и перезавернуть им DEK.
    Возвращает (new_header, recovery_code)."""
    return _rewrap_recovery(dek, header)


def is_encrypted_file(path: str) -> bool:
    """Быстрая проверка по сигнатуре, зашифрован ли файл-БД."""
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False
