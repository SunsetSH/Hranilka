"""Регрессии аудита: шрифт в HTML-экспорте, _valid_int, URI бэкапа, 29 февраля,
rollback при restore, метка обновления, TLS-ошибки апдейтера."""
import random
import ssl
from datetime import date

import pytest

from hranilka.core import theme_io
from hranilka.generators import pd_generator
from hranilka.services import backup, export, update_install as ui, updater
from hranilka.services.export import Options
from hranilka.services.updater import UpdateError


# --- темы / экспорт ---------------------------------------------------------

@pytest.mark.parametrize("name", ["Segoe UI", "Courier-New", "DejaVu Sans Mono.ttf",
                                  "Шрифт Пример", "Lucida Console"])
def test_valid_font_accepts_real_names(name):
    assert theme_io._valid_font(name) == name


@pytest.mark.parametrize("name", ["a'}</style><script>x</script>", "a;b", "a<b", 'a"b',
                                  "a\\b", "a{b}", "", "  ", 5, None])
def test_valid_font_rejects_injection(name):
    assert theme_io._valid_font(name) is theme_io._MISSING


@pytest.mark.parametrize("raw", [float("inf"), 1e999, float("-inf")])
def test_valid_int_overflow_is_skipped(raw):
    assert theme_io._valid_int("font_size")(raw) is theme_io._MISSING


def test_load_infinity_json_does_not_crash():
    text = ('{"format":"hranilka-theme","version":1,"theme":'
            '{"font_size":1e999,"text_color":"#112233"}}')
    data = theme_io.load(text)
    assert "font_size" in data.skipped and data.values["text_color"] == "#112233"
    with pytest.raises(theme_io.ThemeIOError):
        theme_io.load('{"format":"hranilka-theme","version":1,"theme":{"font_size":1e999}}')


def _html(theme, db):
    return "".join(export._html_chunks(db.export_subtree(),
                                       Options(theme=theme, title="T")))


def test_html_export_escapes_bad_font_and_bad_size(db):
    html = _html({"font": "x'}</style><script>alert(1)</script>", "font_size": "abc"}, db)
    assert "<script>" not in html and "font-family:'Consolas'" in html
    assert "font-size:14px" in html


def test_html_export_keeps_good_font(db):
    assert "font-family:'Шрифт Пример-2.0'" in _html({"font": "Шрифт Пример-2.0"}, db)


# --- backup -----------------------------------------------------------------

def _make_db(path):
    from hranilka.data.database import Database
    d = Database(str(path))
    d.connect()
    d.create_tables()
    d.conn.commit()
    d.close(persist=False)


def test_is_valid_db_special_chars_in_path(tmp_path):
    folder = tmp_path / "a#b c%d"
    folder.mkdir()
    db = folder / "x#1%41.db"
    _make_db(db)
    assert backup._is_valid_db(db)


def test_restore_copy_failure_wipes_rollback(tmp_path, monkeypatch):
    dbp = tmp_path / "hranilka.db"
    _make_db(dbp)
    cand = tmp_path / "cand.db"
    _make_db(cand)
    real = backup._copy_durable

    def flaky(src, dst):
        real(src, dst)
        if str(dst).endswith(".rollback"):
            raise OSError("диск")

    monkeypatch.setattr(backup, "_copy_durable", flaky)
    with pytest.raises(OSError):
        backup.restore_backup(str(cand), str(dbp))
    assert not (tmp_path / "hranilka.db.rollback").exists()
    assert not (tmp_path / "hranilka.db.restore-tmp").exists()


def test_restore_failed_validation_without_prev_removes_dst(tmp_path, monkeypatch):
    cand = tmp_path / "cand.db"
    _make_db(cand)
    dst = tmp_path / "new.db"
    calls = []

    def fake_valid(p):
        calls.append(p)
        return len(calls) == 1          # кандидат ок, результат — нет

    monkeypatch.setattr(backup, "_is_valid_db", fake_valid)
    with pytest.raises(ValueError):
        backup.restore_backup(str(cand), str(dst))
    assert not dst.exists()


# --- генератор --------------------------------------------------------------

def test_random_birth_date_on_feb_29(monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2024, 2, 29)

    monkeypatch.setattr(pd_generator, "date", FakeDate)
    for seed in range(30):
        d = pd_generator._random_birth_date(random.Random(seed))
        assert d < date(2024, 2, 29)


# --- обновление -------------------------------------------------------------

def test_cleanup_keeps_marker_in_confirming_process(tmp_path, monkeypatch):
    exe = tmp_path / "Hranilka.exe"
    exe.write_bytes(b"x")
    marker = tmp_path / ui.CONFIRM_MARKER
    marker.write_bytes(b"")
    monkeypatch.setattr(updater, "running_exe", lambda: exe)
    monkeypatch.setattr(ui, "BASE_DIR", tmp_path)
    monkeypatch.setattr(ui.sys, "frozen", True, raising=False)
    ui.cleanup_leftovers(["Hranilka.exe", ui.CONFIRM_ARG])
    assert marker.exists()
    ui.cleanup_leftovers(["Hranilka.exe"])        # обычный запуск чистит забытую метку
    assert not marker.exists()


def test_ssl_context_falls_back_without_certifi(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "certifi":
            raise ImportError(name)
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert isinstance(updater._ssl_context(), ssl.SSLContext)


def test_fetch_latest_ssl_error_maps_to_update_error(monkeypatch):
    from urllib.error import URLError

    def boom(*_a):
        raise URLError(ssl.SSLCertVerificationError("bad cert"))

    monkeypatch.setattr(updater, "_read_limited", boom)
    with pytest.raises(UpdateError, match="защищённое"):
        updater.fetch_latest()


def test_download_ssl_error_maps_to_update_error(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise ssl.SSLError("handshake")

    monkeypatch.setattr(updater, "_read_limited", boom)
    release = updater.ReleaseInfo("9.0", "", "https://github.com/x", "https://github.com/e",
                                  10, "https://github.com/s")
    with pytest.raises(UpdateError, match="защищённое"):
        updater.download(release, tmp_path, lambda d, t: None, lambda: False)
    assert list(tmp_path.iterdir()) == []


def test_restore_keeps_rollback_when_post_check_raises(tmp_path, monkeypatch):
    # Сбой проверки ПОСЛЕ подмены dst: rollback — единственная копия прежней БД.
    from hranilka.services import backup as bk
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    src.write_bytes(b"new")
    dst.write_bytes(b"old")
    calls = []

    def valid(path):
        calls.append(path)
        if len(calls) > 1:
            raise OSError("boom")
        return True

    monkeypatch.setattr(bk, "_is_valid_db", valid)
    with pytest.raises(OSError):
        bk.restore_backup(str(src), str(dst))
    assert (tmp_path / "dst.db.rollback").read_bytes() == b"old"


def test_font_whitelist_real_names_and_injection():
    from hranilka.core.theme_io import is_safe_font_name as ok
    assert ok("Terminus (TTF)") and ok("M+ 1m") and ok("Cascadia Code")
    assert not ok("x'}</style>") and not ok("a\b") and not ok("   ")
