"""Установка обновления: снимок данных, замена exe, подтверждение, откат."""
import hashlib
import os
import time

import pytest

from hranilka.services import update_install as ui
from hranilka.services import updater
from hranilka.services.updater import StagedUpdate

OLD = b"old exe"
NEW = b"new exe"


class FakeProc:
    pid = 4242

    def __init__(self, code=None):
        self.code = code
        self.killed = False

    def poll(self):
        return self.code

    def kill(self):
        self.killed = True
        self.code = -9

    def wait(self, timeout=None):
        return self.code


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    exe = app / "Hranilka.exe"
    exe.write_bytes(OLD)
    staged_path = app / "Hranilka.exe.update"
    staged_path.write_bytes(NEW)
    db = app / "hranilka.db"
    db.write_bytes(b"db")
    cfg = app / "config.json"
    cfg.write_bytes(b"{}")
    monkeypatch.setattr(ui, "BASE_DIR", app)
    monkeypatch.setattr(ui, "CONFIG_FILE", cfg)
    monkeypatch.setattr(ui, "_POLL_SECS", 0.001)
    monkeypatch.setattr(ui, "_REPLACE_DELAY_SECS", 0.001)
    monkeypatch.setattr(ui, "_pending", None)
    monkeypatch.setattr(ui, "_startup_state", None)
    monkeypatch.setattr(updater, "running_exe", lambda: exe)
    monkeypatch.setattr(ui.subprocess, "run", lambda *a, **kw: None)
    staged = StagedUpdate(staged_path, hashlib.sha256(NEW).hexdigest(), "9.0")
    return type("Env", (), dict(app=app, exe=exe, staged=staged, db=db, cfg=cfg))


def _spawn_confirming(calls, seen=None):
    """Новая версия поднялась и записала метку."""
    def spawn(exe, arg):
        calls.append(arg)
        if seen is not None:
            seen.append(exe.read_bytes())
        exe.with_name(ui.CONFIRM_MARKER).write_bytes(b"")
        return FakeProc()
    return spawn


def test_success_swaps_exe_keeps_old_and_snapshots(env, monkeypatch):
    calls, seen = [], []
    monkeypatch.setattr(ui, "_spawn", _spawn_confirming(calls, seen))
    assert ui.install(env.staged, env.exe) is None
    assert calls == [ui.CONFIRM_ARG] and seen == [NEW]
    assert env.exe.read_bytes() == NEW
    assert (env.app / "Hranilka.exe.old").read_bytes() == OLD
    assert not env.staged.path.exists()
    assert not (env.app / ui.CONFIRM_MARKER).exists()
    assert (env.app / "hranilka.db.pre-update").read_bytes() == b"db"
    assert (env.app / "config.json.pre-update").read_bytes() == b"{}"
    assert env.db.read_bytes() == b"db"           # данные не тронуты


def test_process_died_without_marker_rolls_back(env, monkeypatch):
    proc = FakeProc(code=1)
    monkeypatch.setattr(ui, "_spawn", lambda exe, arg: proc)
    assert ui.install(env.staged, env.exe) == env.exe
    assert env.exe.read_bytes() == OLD
    assert not (env.app / "Hranilka.exe.old").exists()


def test_hung_process_killed_with_children_and_rolled_back(env, monkeypatch):
    proc, commands = FakeProc(), []
    monkeypatch.setattr(ui, "_spawn", lambda exe, arg: proc)
    monkeypatch.setattr(ui.subprocess, "run", lambda cmd, **kw: commands.append(cmd))
    assert ui.install(env.staged, env.exe, timeout=0.02) == env.exe
    assert proc.killed and env.exe.read_bytes() == OLD
    assert commands[0][0].lower().endswith(r"system32\taskkill.exe")
    assert commands[0][1:] == ["/F", "/T", "/PID", "4242"]


def test_spawn_failure_rolls_back(env, monkeypatch):
    def boom(exe, arg):
        raise OSError("не PE-файл")
    monkeypatch.setattr(ui, "_spawn", boom)
    assert ui.install(env.staged, env.exe) == env.exe
    assert env.exe.read_bytes() == OLD


def test_stale_marker_is_not_confirmation(env, monkeypatch):
    (env.app / ui.CONFIRM_MARKER).write_bytes(b"")
    monkeypatch.setattr(ui, "_spawn", lambda exe, arg: FakeProc(code=1))
    assert ui.install(env.staged, env.exe) == env.exe
    assert env.exe.read_bytes() == OLD


def test_tampered_after_verification_refused(env, monkeypatch):
    env.staged.path.write_bytes(b"malware")
    monkeypatch.setattr(ui, "_spawn", lambda *a: pytest.fail("запуск запрещён"))
    assert ui.install(env.staged, env.exe) == env.exe
    assert env.exe.read_bytes() == OLD and not env.staged.path.exists()


def test_snapshot_failure_aborts_before_swap(env, monkeypatch):
    def boom(src, dst):
        raise OSError("нет места")
    monkeypatch.setattr(ui.bk, "_copy_durable", boom)
    monkeypatch.setattr(ui, "_spawn", lambda *a: pytest.fail("запуск запрещён"))
    assert ui.install(env.staged, env.exe) == env.exe
    assert env.exe.read_bytes() == OLD
    assert not (env.app / "hranilka.db.pre-update.tmp").exists()


def test_second_rename_failure_restores_exe(env, monkeypatch):
    real = os.replace

    def flaky(src, dst):
        if str(src).endswith(".update"):
            raise OSError("занят")
        real(src, dst)
    monkeypatch.setattr(ui.os, "replace", flaky)
    assert ui.install(env.staged, env.exe) == env.exe
    assert env.exe.read_bytes() == OLD


def test_replace_retries_antivirus_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "_REPLACE_DELAY_SECS", 0)
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_bytes(b"x")
    real, fails = os.replace, [1, 2]

    def flaky(a, b):
        if fails:
            fails.pop()
            raise PermissionError("занят")
        real(a, b)
    monkeypatch.setattr(ui.os, "replace", flaky)
    ui._replace(src, dst)
    assert dst.read_bytes() == b"x"


def test_finish_pending_restarts_old_on_failure(env, monkeypatch):
    calls = []

    def spawn(exe, arg):
        calls.append(arg)
        return FakeProc(code=1)
    monkeypatch.setattr(ui, "_spawn", spawn)
    ui.request_install(env.staged)
    assert ui.finish_pending() == 0
    assert calls == [ui.CONFIRM_ARG, ui.FAILED_ARG]
    assert env.exe.read_bytes() == OLD


def test_failed_rollback_restarts_old_copy(env, monkeypatch):
    """Новая версия упала и .old не вернулся: запускать надо именно .old."""
    calls, real = [], os.replace

    def locked_rollback(src, dst):
        if str(src).endswith(ui.OLD_SUFFIX):
            raise PermissionError("занят")
        real(src, dst)

    def spawn(exe, arg):
        calls.append((exe.name, arg))
        return FakeProc(code=1)
    monkeypatch.setattr(ui, "_REPLACE_RETRIES", 2)
    monkeypatch.setattr(ui.os, "replace", locked_rollback)
    monkeypatch.setattr(ui, "_spawn", spawn)
    ui.request_install(env.staged)
    assert ui.finish_pending() == 0
    assert calls == [("Hranilka.exe", ui.CONFIRM_ARG),
                     ("Hranilka.exe.old", ui.FAILED_ARG)]


def test_finish_pending_noop_without_request(env, monkeypatch):
    monkeypatch.setattr(ui, "_spawn", lambda *a: pytest.fail("нечего запускать"))
    assert ui.finish_pending() == 0


def test_cancel_install_removes_file(env):
    ui.request_install(env.staged)
    ui.cancel_install()
    assert not env.staged.path.exists()
    assert ui.finish_pending() == 0


def test_confirm_startup_writes_marker(env):
    ui.confirm_startup(["Hranilka.exe", ui.CONFIRM_ARG])
    assert (env.app / ui.CONFIRM_MARKER).exists()
    assert ui.take_startup_state() == "updated"
    assert ui.take_startup_state() is None


def test_confirm_startup_failed_and_plain(env):
    ui.confirm_startup(["Hranilka.exe", ui.FAILED_ARG])
    assert ui.take_startup_state() == "failed"
    ui.confirm_startup(["Hranilka.exe"])
    assert ui.take_startup_state() is None
    assert not (env.app / ui.CONFIRM_MARKER).exists()


def test_cleanup_removes_leftovers_and_expired_snapshot(env, monkeypatch):
    monkeypatch.setattr(ui.sys, "frozen", True, raising=False)
    old = env.app / "Hranilka.exe.old"
    old.write_bytes(OLD)
    (env.app / "Hranilka.exe.update.part").write_bytes(b"x")
    expired = env.app / "hranilka.db.pre-update"
    expired.write_bytes(b"db")
    stale = time.time() - ui._SNAPSHOT_MAX_AGE_SECS - 60
    os.utime(expired, (stale, stale))
    fresh = env.app / "config.json.pre-update"
    fresh.write_bytes(b"{}")
    ui.cleanup_leftovers()
    assert not old.exists() and not expired.exists()
    assert not env.staged.path.exists()
    assert not (env.app / "Hranilka.exe.update.part").exists()
    assert fresh.exists() and env.exe.exists() and env.db.exists()


def test_cleanup_noop_in_source_run(env):
    old = env.app / "Hranilka.exe.old"
    old.write_bytes(OLD)
    ui.cleanup_leftovers()
    ui.remove_old_exe()
    assert old.exists()


def test_spawn_falls_back_when_job_forbids_breakaway(env, monkeypatch):
    flags = []

    def fake_popen(cmd, **kw):
        flags.append(kw["creationflags"])
        if len(flags) == 1:
            raise PermissionError("job без breakaway")
        return FakeProc()
    monkeypatch.setattr(ui.subprocess, "Popen", fake_popen)
    ui._spawn(env.exe, ui.CONFIRM_ARG)
    breakaway = ui.subprocess.CREATE_BREAKAWAY_FROM_JOB
    assert flags[0] & breakaway and not flags[1] & breakaway


def test_spawn_resets_pyinstaller_environment(env, monkeypatch):
    seen = {}

    def fake_popen(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"], cwd=kw["cwd"])
        return FakeProc()
    monkeypatch.setattr(ui.subprocess, "Popen", fake_popen)
    ui._spawn(env.exe, ui.CONFIRM_ARG)
    assert seen["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert seen["cmd"] == [str(env.exe), ui.CONFIRM_ARG]
    assert seen["cwd"] == str(env.app)
