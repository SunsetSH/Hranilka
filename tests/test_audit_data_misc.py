"""Аудит 2026-11: S4/lock_on_session_lock (config), paths fallback,
S11 (instance_lock)."""
import os
from pathlib import Path

import pytest

from hranilka.core import config as config_mod
from hranilka.core import paths
from hranilka.core.instance_lock import InstanceLock, VaultLockedError


# ─── config ──────────────────────────────────────────────────────────────────

def test_new_config_gets_safe_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    c = config_mod.Config()
    assert c.get("clipboard_clear_secs") == 30
    assert c.get("idle_lock_mins") == 10


def test_existing_config_keeps_old_behavior(tmp_path, monkeypatch):
    f = tmp_path / "config.json"
    f.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_FILE", f)
    c = config_mod.Config()
    assert c.get("clipboard_clear_secs") == 0
    assert c.get("idle_lock_mins") == 0


def test_existing_config_explicit_values_respected(tmp_path, monkeypatch):
    f = tmp_path / "config.json"
    f.write_text('{"clipboard_clear_secs": 5, "idle_lock_mins": 2}', encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_FILE", f)
    c = config_mod.Config()
    assert (c.get("clipboard_clear_secs"), c.get("idle_lock_mins")) == (5, 2)


def test_lock_on_session_lock_default_and_sanitized(tmp_path, monkeypatch):
    f = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_FILE", f)
    c = config_mod.Config()
    assert c.get("lock_on_session_lock") is False
    assert c._sanitize({"lock_on_session_lock": "true"})["lock_on_session_lock"] is True
    assert c._sanitize({"lock_on_session_lock": "garbage"})["lock_on_session_lock"] is False


# ─── paths ───────────────────────────────────────────────────────────────────

def test_writable_base_dir_unchanged(tmp_path):
    assert paths.resolve_base_dir(tmp_path, appdata=str(tmp_path / "ad")) == tmp_path
    assert not (tmp_path / "ad").exists()      # fallback не создаётся зря


def test_unwritable_base_dir_falls_back_to_appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "_is_writable", lambda p: Path(p) != tmp_path)
    got = paths.resolve_base_dir(tmp_path, appdata=str(tmp_path / "ad"))
    assert got == tmp_path / "ad" / "Hranilka" and got.is_dir()


def test_no_appdata_keeps_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "_is_writable", lambda p: False)
    monkeypatch.delenv("APPDATA", raising=False)
    assert paths.resolve_base_dir(tmp_path) == tmp_path


def test_is_writable_real_probe(tmp_path):
    assert paths._is_writable(tmp_path) is True
    assert paths._is_writable(tmp_path / "missing") is False


# ─── instance_lock ───────────────────────────────────────────────────────────

def test_lock_exclusive_and_reports_holder(tmp_path):
    db = str(tmp_path / "v.db")
    a = InstanceLock(db)
    a.acquire()
    try:
        b = InstanceLock(db)
        with pytest.raises(VaultLockedError) as ei:
            b.acquire()
        assert f"pid={os.getpid()}" in ei.value.holder
        assert ei.value.args[0] == db + ".lock"
    finally:
        a.release()
    b.acquire()                    # после release свободно
    b.release()
    b.release()                    # идемпотентно


def test_lock_info_cleared_on_release(tmp_path):
    db = str(tmp_path / "v.db")
    with InstanceLock(db):
        pass
    assert os.path.getsize(db + ".lock") == 0
