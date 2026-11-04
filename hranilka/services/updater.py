"""Проверка, загрузка и проверка подлинности обновлений с GitHub Releases. Без Qt.

В сеть модуль выходит только по явному действию: проверка при запуске
включается чекбоксом check_updates. Функции блокирующие — вызывать из фонового
потока. Замена exe — в services/update_install.py, разбор
рисков — в docs/Автообновление.md.

Загруженный файл принимается, только если совпали ВСЕ условия: SHA256 из
релиза, целостность Authenticode-подписи, нет данных вне подписи, сертификат тот
же, что у запущенного exe, версия внутри файла равна версии релиза. SHA256 лежит рядом с exe и от
подмены релиза не защищает — от неё защищает закрытый ключ подписи, которого на
GitHub нет."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from hranilka import __version__

logger = logging.getLogger(__name__)

REPO = "SunsetSH/Hranilka"
RELEASES_URL = f"https://github.com/{REPO}/releases"
LATEST_API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_EXE = "Hranilka.exe"
ASSET_SHA256 = ASSET_EXE + ".sha256"

STAGED_SUFFIX = ".update"
PART_SUFFIX = ".update.part"

_ALLOWED_HOSTS = ("github.com", "api.github.com")
_ALLOWED_HOST_SUFFIX = ".githubusercontent.com"   # редиректы загрузки ассетов
_TIMEOUT_SECS = 20
_CHUNK = 256 * 1024
_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_SHA_BYTES = 1024
_MAX_EXE_BYTES = 256 * 1024 * 1024
_MAX_NOTES_CHARS = 20000
_DISK_RESERVE_BYTES = 32 * 1024 * 1024
_SIGNATURE_TIMEOUT_SECS = 60
# Подпись самоподписанная: на машине без доверенного сертификата Windows даёт
# UnknownError/NotTrusted, но испорченный файл — всегда HashMismatch.
_SIGNATURE_OK = ("Valid", "UnknownError", "NotTrusted")
_PS_READ_SIGNATURE = (
    "$p=$env:HRANILKA_VERIFY_PATH;"
    "$s=Get-AuthenticodeSignature -LiteralPath $p;"
    "$v=(Get-Item -LiteralPath $p).VersionInfo.FileVersion;"
    "@{status=[string]$s.Status;"
    "thumbprint=[string]$s.SignerCertificate.Thumbprint;"
    "version=[string]$v}|ConvertTo-Json -Compress"
)


class UpdateError(Exception):
    """Ошибка обновления; текст показывается пользователю."""


class UpdateCancelled(Exception):
    """Загрузку отменил пользователь."""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    notes: str
    page_url: str
    exe_url: str
    exe_size: int
    sha256_url: str


@dataclass(frozen=True)
class StagedUpdate:
    """Проверенный файл новой версии рядом с запущенным exe."""
    path: Path
    sha256: str
    version: str


@dataclass(frozen=True)
class SignatureInfo:
    status: str
    thumbprint: str
    version: str


def parse_version(text: Any) -> Optional[tuple[int, ...]]:
    """'v1.6' -> (1, 6). Хвостовые нули отбрасываются: 1.6 == 1.6.0.0.
    Не версия (в том числе '1.6-beta') — None."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"[vV]?(\d{1,6}(?:\.\d{1,6}){0,3})", text.strip())
    if match is None:
        return None
    parts = [int(p) for p in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(candidate: Any, current: str = __version__) -> bool:
    """True только для строго большей версии: ни откат, ни переустановка той же
    версии через подделанный ответ невозможны."""
    new, cur = parse_version(candidate), parse_version(current)
    return new is not None and cur is not None and new > cur


def _is_allowed_url(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (
        host in _ALLOWED_HOSTS or host.endswith(_ALLOWED_HOST_SUFFIX))


def _require_allowed_url(url: Any) -> None:
    if not _is_allowed_url(url):
        raise UpdateError("Получен адрес вне GitHub — обновление отклонено.")


def _ssl_context() -> Any:
    """Системные корневые сертификаты плюс certifi: на чистой Windows нужного
    корня может не быть в хранилище, пока его не запросит браузер."""
    import ssl

    import certifi
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def _open(url: str, accept: str) -> Any:
    """HTTPS-запрос; каждый адрес, включая редиректы, проходит проверку хоста."""
    import urllib.request

    class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                             headers: Any, newurl: str) -> Any:
            _require_allowed_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    _require_allowed_url(url)
    opener = urllib.request.build_opener(
        _CheckedRedirect(), urllib.request.HTTPSHandler(context=_ssl_context()))
    request = urllib.request.Request(url, headers={
        "Accept": accept, "User-Agent": f"Hranilka/{__version__}"})
    return opener.open(request, timeout=_TIMEOUT_SECS)


def _read_limited(url: str, accept: str, limit: int) -> bytes:
    with _open(url, accept) as response:
        data: bytes = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("Ответ GitHub слишком большой — обновление отклонено.")
    return data


def _network_error(error: Exception) -> UpdateError:
    logger.warning("Обновления: ошибка сети: %s", error)
    return UpdateError("Не удалось связаться с GitHub. Проверьте подключение "
                       "к интернету и повторите позже.")


def _asset_by_name(assets: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(assets, list):
        return {}
    return {a["name"]: a for a in assets
            if isinstance(a, dict) and isinstance(a.get("name"), str)}


def parse_release(payload: Any, current: str = __version__) -> Optional[ReleaseInfo]:
    """ReleaseInfo из ответа GitHub, если есть более новый стабильный релиз.

    Ответ — данные извне: типы и адреса проверяются здесь. Релиз без пары
    exe + .sha256 не считается обновлением."""
    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        return None
    tag = payload.get("tag_name")
    if not is_newer(tag, current):
        return None
    assets = _asset_by_name(payload.get("assets"))
    exe, sha = assets.get(ASSET_EXE), assets.get(ASSET_SHA256)
    if exe is None or sha is None:
        logger.warning("Обновления: в релизе %s нет %s и %s",
                       tag, ASSET_EXE, ASSET_SHA256)
        return None
    exe_url, sha_url = exe.get("browser_download_url"), sha.get("browser_download_url")
    size = exe.get("size")
    if not (_is_allowed_url(exe_url) and _is_allowed_url(sha_url)):
        logger.warning("Обновления: адреса ассетов релиза %s вне GitHub", tag)
        return None
    if not isinstance(size, int) or not 0 < size <= _MAX_EXE_BYTES:
        logger.warning("Обновления: недопустимый размер exe в релизе %s: %r", tag, size)
        return None
    page_url = payload.get("html_url")
    if not (isinstance(page_url, str)
            and page_url.startswith(f"{RELEASES_URL}/tag/")
            and _is_allowed_url(page_url)):
        page_url = RELEASES_URL
    notes = payload.get("body")
    notes = notes[:_MAX_NOTES_CHARS] if isinstance(notes, str) else ""
    return ReleaseInfo(str(tag).lstrip("vV"), notes.replace("\r\n", "\n"),
                       page_url, str(exe_url), size, str(sha_url))


def fetch_latest() -> Optional[ReleaseInfo]:
    """Новый релиз или None, если обновлений нет."""
    from urllib.error import HTTPError, URLError
    try:
        raw = _read_limited(LATEST_API_URL, "application/vnd.github+json",
                            _MAX_JSON_BYTES)
        return parse_release(json.loads(raw.decode("utf-8")))
    except HTTPError as e:
        if e.code == 404:                 # ни одного опубликованного релиза
            return None
        if e.code in (403, 429):
            logger.warning("Обновления: лимит запросов GitHub (%s)", e.code)
            raise UpdateError("GitHub временно ограничил запросы. "
                              "Повторите проверку позже.") from e
        raise _network_error(e) from e
    except (URLError, HTTPException, OSError) as e:
        raise _network_error(e) from e
    except ValueError as e:               # JSONDecodeError, UnicodeDecodeError
        logger.warning("Обновления: некорректный ответ GitHub: %s", e)
        raise UpdateError("GitHub вернул некорректный ответ.") from e


def running_exe() -> Path:
    return Path(sys.executable).resolve()


def auto_update_blocker() -> Optional[str]:
    """Почему автообновление недоступно (текст для окна) или None."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return "Автообновление работает только в собранном Hranilka.exe."
    folder = running_exe().parent
    probe = folder / ".hranilka-update-probe.tmp"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return ("Нет прав на запись в папку программы. "
                "Обновите программу вручную.")
    return None


def parse_sha256(text: str) -> str:
    """Хэш из файла формата '<hex> *Hranilka.exe'."""
    match = re.match(r"\s*([0-9a-fA-F]{64})(?:\s|$)", text)
    if match is None:
        raise UpdateError("Файл контрольной суммы релиза повреждён.")
    return match.group(1).lower()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discard(path: Path) -> None:
    """Удалить файл загрузки, если он есть (идемпотентно)."""
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Обновления: не удалось удалить %s: %s", path, e)


def _check_free_space(folder: Path, size: int) -> None:
    needed = size + _DISK_RESERVE_BYTES
    try:
        free = shutil.disk_usage(folder).free
    except OSError as e:
        logger.warning("Обновления: не удалось узнать свободное место: %s", e)
        return
    if free < needed:
        raise UpdateError("Недостаточно места на диске для обновления.")


def _download_to(release: ReleaseInfo, part: Path,
                 progress: Callable[[int, int], None],
                 cancelled: Callable[[], bool]) -> str:
    """Скачать exe в part; вернуть его SHA256. Размер не может выйти за
    объявленный в релизе."""
    digest = hashlib.sha256()
    done = 0
    with _open(release.exe_url, "application/octet-stream") as response, \
            open(part, "wb") as out:
        while True:
            if cancelled():
                raise UpdateCancelled()
            try:
                chunk = response.read(_CHUNK)
            except (HTTPException, OSError) as e:
                raise _network_error(e) from e
            if not chunk:
                break
            done += len(chunk)
            if done > release.exe_size:
                raise UpdateError("Файл обновления больше заявленного размера.")
            out.write(chunk)
            digest.update(chunk)
            progress(done, release.exe_size)
        out.flush()
        os.fsync(out.fileno())
    if done != release.exe_size:
        raise UpdateError("Загрузка прервалась: файл обновления неполный.")
    return digest.hexdigest()


def download(release: ReleaseInfo, folder: Path,
             progress: Callable[[int, int], None],
             cancelled: Callable[[], bool]) -> StagedUpdate:
    """Скачать exe рядом с программой (тот же том — замена переименованием) и
    сверить SHA256. Неполный или неверный файл на диске не остаётся."""
    from urllib.error import URLError
    _check_free_space(folder, release.exe_size)
    final = folder / (ASSET_EXE + STAGED_SUFFIX)
    part = folder / (ASSET_EXE + PART_SUFFIX)
    try:
        sha_text = _read_limited(release.sha256_url, "application/octet-stream",
                                 _MAX_SHA_BYTES).decode("ascii", "replace")
        expected = parse_sha256(sha_text)
        actual = _download_to(release, part, progress, cancelled)
        if actual != expected:
            raise UpdateError("Контрольная сумма файла не совпала — файл отклонён.")
        os.replace(part, final)
    except (URLError, HTTPException, TimeoutError, ConnectionError) as e:
        raise _network_error(e) from e
    except OSError as e:
        logger.error("Обновления: ошибка записи файла: %s", e)
        raise UpdateError(f"Не удалось сохранить обновление: {e.strerror or e}") from e
    finally:
        discard(part)
    return StagedUpdate(final, expected, release.version)


def _der_length(blob: bytes) -> int:
    """Полная длина DER-значения в начале blob."""
    if len(blob) < 2:
        raise ValueError("короткий DER")
    first = blob[1]
    if first < 0x80:
        return 2 + first
    count = first & 0x7F
    if not 1 <= count <= 4 or len(blob) < 2 + count:
        raise ValueError("длина DER")
    return 2 + count + int.from_bytes(blob[2:2 + count], "big")


def check_no_unsigned_data(path: Path) -> None:
    """В файле нет данных вне подписи.

    Authenticode не хэширует таблицу сертификатов, поэтому дописанное в её конец
    подпись не ломает, а onefile-загрузчик ищет архив с конца файла и выполнил
    бы именно дописанное. Таблица обязана заканчивать файл и содержать ровно одну
    подпись с выравниванием нулями (до 8 байт)."""
    data = Path(path).read_bytes()
    try:
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise ValueError("нет заголовка PE")
        magic = struct.unpack_from("<H", data, pe + 24)[0]
        directories = pe + 24 + (112 if magic == 0x20B else 96)
        offset, size = struct.unpack_from("<II", data, directories + 4 * 8)
        if size == 0 or offset + size != len(data):
            raise ValueError("таблица сертификатов не в конце файла")
        length = struct.unpack_from("<I", data, offset)[0]
        if length != size:
            raise ValueError("в таблице сертификатов не одна запись")
        table = data[offset + 8:offset + size]
        padding = table[_der_length(table):]
        if len(padding) >= 8 or padding.strip(b"\0"):
            raise ValueError("данные после подписи")
    except (ValueError, struct.error) as e:
        logger.error("Обновления: структура файла отклонена: %s", e)
        raise UpdateError("Файл обновления содержит данные вне подписи.") from e


def _powershell_path() -> str:
    """Полный путь из системной папки: поиск по PATH и рядом с exe мог бы
    запустить подложенный файл."""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(root, "System32", "WindowsPowerShell", "v1.0",
                        "powershell.exe")


def read_signature(path: Path) -> SignatureInfo:
    """Подпись Authenticode и версия файла. Путь передаётся через окружение,
    а не в тексте команды — имя папки не может стать кодом PowerShell."""
    env = dict(os.environ, HRANILKA_VERIFY_PATH=str(path))
    try:
        result = subprocess.run(
            [_powershell_path(), "-NoProfile", "-NonInteractive", "-Command",
             _PS_READ_SIGNATURE],
            env=env, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=_SIGNATURE_TIMEOUT_SECS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        logger.error("Обновления: проверка подписи не запустилась: %s", e)
        raise UpdateError("Не удалось проверить подпись файла.") from e
    try:
        data = json.loads(result.stdout.decode("utf-8", "replace").lstrip("\ufeff"))
        return SignatureInfo(str(data["status"]), str(data["thumbprint"]).upper(),
                             str(data["version"]))
    except (ValueError, KeyError, TypeError) as e:
        logger.error("Обновления: некорректный ответ проверки подписи (код %s): %s",
                     result.returncode,
                     result.stderr.decode("utf-8", "replace")[:500])
        raise UpdateError("Не удалось проверить подпись файла.") from e


def verify_package(path: Path, version: str, current_exe: Path) -> None:
    """Подпись цела, издатель тот же, что у запущенного exe, версия та же, что
    у релиза (иначе под новым тегом можно выложить старый подписанный файл)."""
    new = read_signature(path)
    if new.status not in _SIGNATURE_OK or not new.thumbprint:
        logger.error("Обновления: подпись файла отклонена: %s", new.status)
        raise UpdateError("Подпись файла обновления недействительна.")
    check_no_unsigned_data(path)
    current = read_signature(current_exe)
    if not current.thumbprint:
        raise UpdateError("Запущенная программа не подписана, поэтому издателя "
                          "обновления проверить нечем.")
    if new.thumbprint != current.thumbprint:
        logger.error("Обновления: другой сертификат: %s, ожидался %s",
                     new.thumbprint, current.thumbprint)
        raise UpdateError("Обновление подписано другим сертификатом.")
    if parse_version(new.version) != parse_version(version):
        logger.error("Обновления: версия файла %s, релиза %s", new.version, version)
        raise UpdateError("Версия файла не совпадает с версией релиза.")


def prepare(release: ReleaseInfo, progress: Callable[[int, int], None],
            cancelled: Callable[[], bool]) -> StagedUpdate:
    """Скачать и полностью проверить обновление. Отказ или отмена удаляют файл."""
    exe = running_exe()
    staged = download(release, exe.parent, progress, cancelled)
    try:
        verify_package(staged.path, release.version, exe)
        if cancelled():
            raise UpdateCancelled()
    except BaseException:
        discard(staged.path)
        raise
    return staged
