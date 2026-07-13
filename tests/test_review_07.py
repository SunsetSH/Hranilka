"""Тесты исправлений ревью 0.7 (GUI-часть, offscreen).

  * M7-06 — UnlockDialog не блокируется на чтении контейнера до показа: байты
    берутся лениво из future только при попытке разблокировки.
"""
import concurrent.futures

import pytest

# Патч-точка cs — в модуле unlock (этап 7: реэкспорт cs из пакета убран).
from hranilka.ui.dialogs import unlock
from hranilka.ui.dialogs.unlock import UnlockDialog

# Общий session-qapp живёт в conftest.py (offscreen).


def _wait_result(qapp, dlg, timeout_s=5.0):
    """Докрутить event-loop до доставки результата фоновой разблокировки
    (H-09: cs.unlock выполняется в потоке, результат приходит queued-сигналом)."""
    import time
    deadline = time.time() + timeout_s
    while dlg.result_data is None and dlg._busy and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def _future(value=None, exc=None, delay=0.0):
    """Future, резолвящийся значением или исключением (опц. с задержкой)."""
    import threading
    fut = concurrent.futures.Future()

    def _set():
        if exc is not None:
            fut.set_exception(exc)
        else:
            fut.set_result(value)

    if delay:
        threading.Timer(delay, _set).start()
    else:
        _set()
    return fut


def test_unlock_dialog_reads_container_lazily_on_attempt(qapp, pure_config, monkeypatch):
    """Байты контейнера отдаются диалогу через future и берутся лишь при попытке
    разблокировки; успешный unlock срабатывает на прочитанных байтах."""
    seen = {}

    def fake_unlock(container, secret, is_recovery=False):
        seen["container"] = container
        seen["secret"] = secret
        return ("db", "dek", "header")          # result_data

    monkeypatch.setattr(unlock.cs, "unlock", fake_unlock)

    dlg = UnlockDialog(pure_config, b"")
    dlg.set_container_future(_future(value=b"CONTAINER-BYTES", delay=0.05))
    dlg._field.setText("secret")
    dlg._attempt()
    _wait_result(qapp, dlg)

    assert seen["container"] == b"CONTAINER-BYTES"   # дождались future
    assert dlg.result_data == ("db", "dek", "header")


def test_unlock_dialog_shows_error_on_read_failure(qapp, pure_config, monkeypatch):
    """OSError из future чтения файла показывается пользователю, unlock не зовётся,
    краха нет."""
    called = {"unlock": False}

    def fake_unlock(*a, **k):
        called["unlock"] = True
        return ("db", "dek", "header")

    monkeypatch.setattr(unlock.cs, "unlock", fake_unlock)

    dlg = UnlockDialog(pure_config, b"")
    dlg.set_container_future(_future(exc=OSError("нет доступа")))
    dlg._field.setText("secret")
    dlg._attempt()

    assert called["unlock"] is False
    assert dlg.result_data is None
    assert "прочитать файл" in dlg._err.text().lower()


def test_unlock_dialog_ready_container_without_future(qapp, pure_config, monkeypatch):
    """Если байты переданы сразу (без future) — прежнее поведение сохранено."""
    seen = {}

    def fake_unlock(container, secret, is_recovery=False):
        seen["container"] = container
        return ("db", "dek", "header")

    monkeypatch.setattr(unlock.cs, "unlock", fake_unlock)

    dlg = UnlockDialog(pure_config, b"READY")
    dlg._field.setText("secret")
    dlg._attempt()
    _wait_result(qapp, dlg)

    assert seen["container"] == b"READY"
    assert dlg.result_data == ("db", "dek", "header")
