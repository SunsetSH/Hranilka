"""Обновления: разбор ответа GitHub, защита адресов/версий, загрузка, подпись."""
import hashlib
import io
import json
import struct
import subprocess

import pytest

from hranilka.services import updater
from hranilka.services.updater import (ReleaseInfo, SignatureInfo, UpdateCancelled,
                                       UpdateError)

SHA_URL = "https://github.com/SunsetSH/Hranilka/releases/download/9.0/Hranilka.exe.sha256"
EXE_URL = "https://github.com/SunsetSH/Hranilka/releases/download/9.0/Hranilka.exe"


def _payload(**over):
    data = {
        "tag_name": "9.0", "draft": False, "prerelease": False, "body": "Новое\r\nещё",
        "html_url": "https://github.com/SunsetSH/Hranilka/releases/tag/9.0",
        "assets": [
            {"name": "Hranilka.exe", "size": 100, "browser_download_url": EXE_URL},
            {"name": "Hranilka.exe.sha256", "size": 80,
             "browser_download_url": SHA_URL},
        ],
    }
    data.update(over)
    return data


@pytest.mark.parametrize("text,expected", [
    ("1.6", (1, 6)), ("v1.6", (1, 6)), ("1.6.0.0", (1, 6)), ("1.5.1", (1, 5, 1)),
    ("10", (10,)), ("0", (0,)),
])
def test_parse_version(text, expected):
    assert updater.parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "1.6-beta", "abc", "1..2", None, 16, "1.2.3.4.5"])
def test_parse_version_rejects_garbage(text):
    assert updater.parse_version(text) is None


def test_is_newer_only_strictly_greater():
    assert updater.is_newer("1.10", "1.9")          # числа, а не строки
    assert updater.is_newer("1.5.1", "1.5")
    assert not updater.is_newer("1.5", "1.5.0")
    assert not updater.is_newer("1.4", "1.5")       # откат запрещён
    assert not updater.is_newer("мусор", "1.5")


def test_parse_release_ok():
    info = updater.parse_release(_payload(), current="1.6")
    assert info.version == "9.0" and info.notes == "Новое\nещё"
    assert info.exe_url == EXE_URL and info.exe_size == 100
    assert info.page_url.endswith("/tag/9.0")


@pytest.mark.parametrize("over", [
    {"tag_name": "1.6"}, {"tag_name": "1.0"}, {"tag_name": None},
    {"draft": True}, {"prerelease": True}, {"assets": []}, {"assets": "x"},
    {"assets": [{"name": "Hranilka.exe", "size": 1,
                 "browser_download_url": EXE_URL}]},
])
def test_parse_release_ignored(over):
    assert updater.parse_release(_payload(**over), current="1.6") is None


def test_parse_release_rejects_non_dict():
    assert updater.parse_release([], current="1.6") is None


@pytest.mark.parametrize("url", [
    "http://github.com/x", "https://evil.com/Hranilka.exe",
    "https://github.com.evil.com/x", "file:///c:/x.exe",
])
def test_parse_release_rejects_foreign_asset_url(url):
    payload = _payload()
    payload["assets"][0]["browser_download_url"] = url
    assert updater.parse_release(payload, current="1.6") is None


@pytest.mark.parametrize("size", [0, -1, "100", None, 10 ** 12])
def test_parse_release_rejects_bad_size(size):
    payload = _payload()
    payload["assets"][0]["size"] = size
    assert updater.parse_release(payload, current="1.6") is None


@pytest.mark.parametrize("page", ["https://evil.com/", "javascript:alert(1)", None,
                                  "https://github.com/other/repo/releases/tag/9"])
def test_foreign_page_url_replaced_by_releases(page):
    info = updater.parse_release(_payload(html_url=page), current="1.6")
    assert info.page_url == updater.RELEASES_URL


def test_notes_are_capped():
    info = updater.parse_release(_payload(body="x" * 10 ** 6), current="1.6")
    assert len(info.notes) == updater._MAX_NOTES_CHARS


@pytest.mark.parametrize("url,ok", [
    ("https://api.github.com/repos", True),
    ("https://release-assets.githubusercontent.com/a", True),
    ("https://objects.githubusercontent.com/a", True),
    ("http://api.github.com/repos", False),
    ("https://githubusercontent.com.evil.org/a", False),
    ("https://evilgithubusercontent.com/a", False),
    (None, False),
])
def test_allowed_urls(url, ok):
    assert updater._is_allowed_url(url) is ok


def test_open_refuses_foreign_url_before_network(monkeypatch):
    monkeypatch.setattr(updater, "_ssl_context",
                        lambda: pytest.fail("сеть не должна затрагиваться"))
    with pytest.raises(UpdateError):
        updater._open("https://evil.com/x", "*/*")


def test_redirect_to_foreign_host_refused(monkeypatch):
    import urllib.request

    captured = {}
    real_build = urllib.request.build_opener

    def spy(*handlers):
        captured["handler"] = handlers[0]
        return real_build(*handlers)

    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(b"")

    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda *h: (spy(*h), Opener())[1])
    updater._open(EXE_URL, "*/*")
    with pytest.raises(UpdateError):
        captured["handler"].redirect_request(
            urllib.request.Request(EXE_URL), None, 302, "Found", {},
            "http://evil.com/Hranilka.exe")


def test_parse_sha256():
    digest = "A" * 64
    assert updater.parse_sha256(f"{digest} *Hranilka.exe\n") == "a" * 64
    assert updater.parse_sha256(digest) == "a" * 64
    with pytest.raises(UpdateError):
        updater.parse_sha256("не хэш")
    with pytest.raises(UpdateError):
        updater.parse_sha256("a" * 65)


def test_fetch_latest_http_errors(monkeypatch):
    from urllib.error import HTTPError, URLError

    def fail_with(error):
        def _raise(*_a):
            raise error
        monkeypatch.setattr(updater, "_read_limited", _raise)

    fail_with(HTTPError("u", 404, "nf", None, None))
    assert updater.fetch_latest() is None
    fail_with(HTTPError("u", 403, "limit", None, None))
    with pytest.raises(UpdateError, match="ограничил"):
        updater.fetch_latest()
    fail_with(URLError("нет сети"))
    with pytest.raises(UpdateError, match="связаться"):
        updater.fetch_latest()


def test_fetch_latest_bad_json(monkeypatch):
    monkeypatch.setattr(updater, "_read_limited", lambda *a: b"<html>")
    with pytest.raises(UpdateError, match="некорректный"):
        updater.fetch_latest()


def test_read_limited_rejects_oversize(monkeypatch):
    monkeypatch.setattr(updater, "_open", lambda *a: io.BytesIO(b"x" * 11))
    with pytest.raises(UpdateError):
        updater._read_limited("u", "*/*", 10)


CONTENT = b"new exe" * 1000
GOOD_SHA = hashlib.sha256(CONTENT).hexdigest()


def _release(size=len(CONTENT)):
    return ReleaseInfo("9.0", "", updater.RELEASES_URL, EXE_URL, size, SHA_URL)


@pytest.fixture
def net(monkeypatch):
    """Подмена сети: exe = CONTENT, sha = state['sha']."""
    state = {"sha": GOOD_SHA, "exe": CONTENT}

    def fake_open(url, accept):
        return io.BytesIO(state["exe"] if url == EXE_URL
                          else f"{state['sha']} *Hranilka.exe\n".encode())
    monkeypatch.setattr(updater, "_open", fake_open)
    return state


def _no_cancel():
    return False


def test_download_ok(net, tmp_path):
    seen = []
    staged = updater.download(_release(), tmp_path, lambda d, t: seen.append(d),
                              _no_cancel)
    assert staged.path.read_bytes() == CONTENT and staged.sha256 == GOOD_SHA
    assert seen[-1] == len(CONTENT)
    assert not (tmp_path / "Hranilka.exe.update.part").exists()


def test_download_wrong_hash_leaves_nothing(net, tmp_path):
    net["sha"] = "0" * 64
    with pytest.raises(UpdateError, match="сумма"):
        updater.download(_release(), tmp_path, lambda d, t: None, _no_cancel)
    assert list(tmp_path.iterdir()) == []


def test_download_truncated_file_rejected(net, tmp_path):
    net["exe"] = CONTENT[:-1]
    with pytest.raises(UpdateError, match="неполный"):
        updater.download(_release(), tmp_path, lambda d, t: None, _no_cancel)
    assert list(tmp_path.iterdir()) == []


def test_download_larger_than_declared_stops(net, tmp_path):
    net["exe"] = CONTENT + b"x"
    with pytest.raises(UpdateError, match="больше"):
        updater.download(_release(), tmp_path, lambda d, t: None, _no_cancel)
    assert list(tmp_path.iterdir()) == []


def test_download_cancel_leaves_nothing(net, tmp_path):
    with pytest.raises(UpdateCancelled):
        updater.download(_release(), tmp_path, lambda d, t: None, lambda: True)
    assert list(tmp_path.iterdir()) == []


def test_download_no_disk_space(net, tmp_path, monkeypatch):
    usage = type("U", (), {"free": 10})
    monkeypatch.setattr(updater.shutil, "disk_usage", lambda p: usage)
    with pytest.raises(UpdateError, match="места"):
        updater.download(_release(), tmp_path, lambda d, t: None, _no_cancel)


def _signatures(monkeypatch, new, current):
    exe = "cur.exe"
    monkeypatch.setattr(
        updater, "read_signature",
        lambda path: current if str(path) == exe else new)
    return exe


GOOD_NEW = SignatureInfo("UnknownError", "ABC", "9.0.0.0")
CURRENT = SignatureInfo("UnknownError", "ABC", "1.6.0.0")


def _pe(tail=b"", der_size=20, padding=4, extra_record=b""):
    """Минимальный PE32+ с таблицей сертификатов в конце."""
    der = b"\x30" + bytes([der_size - 2]) + b"\x01" * (der_size - 2)
    entry = struct.pack("<IHH", 8 + der_size + padding, 0x200, 2) + der + b"\0" * padding
    table = entry + extra_record + tail
    head = bytearray(0x400)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", head, 0x80 + 24, 0x20B)
    struct.pack_into("<II", head, 0x80 + 24 + 112 + 32, len(head), len(table))
    return bytes(head) + table


def _write(tmp_path, data):
    path = tmp_path / "x.exe"
    path.write_bytes(data)
    return path


def test_unsigned_data_check_accepts_clean_table(tmp_path):
    updater.check_no_unsigned_data(_write(tmp_path, _pe()))


@pytest.mark.parametrize("data", [
    _pe() + b"payload",                               # данные за таблицей
    _pe(padding=8),                                   # лишнее выравнивание
    b"not a pe file" * 100,
    b"",
])
def test_unsigned_data_check_rejects(tmp_path, data):
    with pytest.raises(UpdateError, match="вне подписи"):
        updater.check_no_unsigned_data(_write(tmp_path, data))


def test_unsigned_data_check_rejects_payload_inside_table(tmp_path):
    """Дописанное в таблицу сертификатов: подпись цела, но загрузчик его найдёт."""
    data = bytearray(_pe(tail=b"PYZ payload"))
    with pytest.raises(UpdateError, match="вне подписи"):
        updater.check_no_unsigned_data(_write(tmp_path, bytes(data)))


def test_unsigned_data_check_rejects_nonzero_padding(tmp_path):
    data = bytearray(_pe())
    data[-1] = 1
    with pytest.raises(UpdateError, match="вне подписи"):
        updater.check_no_unsigned_data(_write(tmp_path, bytes(data)))


def test_verify_accepts_same_publisher(monkeypatch):
    exe = _signatures(monkeypatch, GOOD_NEW, CURRENT)
    monkeypatch.setattr(updater, "check_no_unsigned_data", lambda p: None)
    updater.verify_package("new.exe", "9.0", exe)


@pytest.mark.parametrize("new,current,message", [
    (SignatureInfo("HashMismatch", "ABC", "9.0.0.0"), CURRENT, "недействительна"),
    (SignatureInfo("NotSigned", "", "9.0.0.0"), CURRENT, "недействительна"),
    (SignatureInfo("Valid", "", "9.0.0.0"), CURRENT, "недействительна"),
    (SignatureInfo("Valid", "DEF", "9.0.0.0"), CURRENT, "другим сертификатом"),
    (SignatureInfo("Valid", "ABC", "1.0.0.0"), CURRENT, "Версия"),
    (GOOD_NEW, SignatureInfo("NotSigned", "", "1.6.0.0"), "не подписана"),
])
def test_verify_rejects(monkeypatch, new, current, message):
    exe = _signatures(monkeypatch, new, current)
    monkeypatch.setattr(updater, "check_no_unsigned_data", lambda p: None)
    with pytest.raises(UpdateError, match=message):
        updater.verify_package("new.exe", "9.0", exe)


def test_prepare_deletes_file_rejected_by_signature(net, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "running_exe", lambda: tmp_path / "Hranilka.exe")

    def reject(*_a):
        raise UpdateError("нет")
    monkeypatch.setattr(updater, "verify_package", reject)
    with pytest.raises(UpdateError):
        updater.prepare(_release(), lambda d, t: None, _no_cancel)
    assert list(tmp_path.iterdir()) == []


def test_download_network_failure_is_not_reported_as_disk_error(tmp_path, monkeypatch):
    import ssl

    class Broken(io.BytesIO):
        def read(self, *_a):
            raise ssl.SSLError("обрыв TLS")

    monkeypatch.setattr(
        updater, "_open",
        lambda url, accept: Broken() if url == EXE_URL
        else io.BytesIO(f"{GOOD_SHA} *x".encode()))
    with pytest.raises(UpdateError, match="связаться"):
        updater.download(_release(), tmp_path, lambda d, t: None, _no_cancel)
    assert list(tmp_path.iterdir()) == []


def test_read_signature_passes_path_via_env(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"])
        out = json.dumps({"status": "Valid", "thumbprint": "ab", "version": "1.6"})
        return subprocess.CompletedProcess(cmd, 0, out.encode(), b"")
    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    path = tmp_path / "a'; calc; '.exe"
    info = updater.read_signature(path)
    assert info == SignatureInfo("Valid", "AB", "1.6")
    assert seen["env"]["HRANILKA_VERIFY_PATH"] == str(path)
    assert str(path) not in " ".join(seen["cmd"])
    assert seen["cmd"][0].lower().endswith(r"system32\windowspowershell\v1.0\powershell.exe")


@pytest.mark.parametrize("stdout", [b"", b"{}", b"not json"])
def test_read_signature_bad_output(monkeypatch, stdout):
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout, b"e"))
    with pytest.raises(UpdateError):
        updater.read_signature("x")


def test_read_signature_timeout(monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    with pytest.raises(UpdateError):
        updater.read_signature("x")


def test_auto_update_blocked_in_source_run():
    assert "собранном" in updater.auto_update_blocker()


def test_auto_update_blocked_without_write_access(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater, "running_exe", lambda: tmp_path / "no" / "H.exe")
    assert "запись" in updater.auto_update_blocker()
    monkeypatch.setattr(updater, "running_exe", lambda: tmp_path / "H.exe")
    assert updater.auto_update_blocker() is None
    assert list(tmp_path.iterdir()) == []
