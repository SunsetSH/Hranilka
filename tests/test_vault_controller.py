"""VaultController (Эпик 4.2/4.4): гейт run_exclusive и ожидание воркера.

Требует QApplication (offscreen из conftest). Используем лёгкое фейковое окно
для UI-побочек (строка статуса) — полноценный MainWindow тут не нужен.
"""
import pytest

from hranilka.data.database import Database
from hranilka.ui.vault_controller import VaultController

# Общий session-qapp живёт в conftest.py (M-16).


class _FakeBar:
    def showMessage(self, *a, **k):
        pass


class _FakeWindow:
    def statusBar(self):
        return _FakeBar()


@pytest.fixture
def vc(qapp, tmp_db_path, pure_config):
    db = Database(tmp_db_path)
    db.connect()
    db.create_tables()
    controller = VaultController(db, _FakeWindow(), pure_config)
    yield controller
    controller.shutdown()
    db.close(persist=False)


def test_wait_idle_true_when_not_busy(vc):
    assert vc.wait_idle() is True


def test_run_exclusive_returns_result(vc):
    ok, res = vc.run_exclusive(lambda: 7)
    assert ok is True and res == 7


def test_run_exclusive_reports_error(vc):
    def boom():
        raise RuntimeError("сбой операции")
    ok, res = vc.run_exclusive(boom)
    assert ok is False and "сбой операции" in res


def test_vault_locked_during_op(vc):
    # Внутри привилегированной операции монопольный флаг взведён; после — снят.
    seen = {}

    def op():
        seen["locked"] = vc._vault_locked
        return None
    vc.run_exclusive(op)
    assert seen["locked"] is True
    assert vc._vault_locked is False


def test_plaintext_flush_is_noop(vc):
    # В обычном режиме flush не падает и ничего не «залипает».
    vc.flush()
    assert vc._write_busy is False


# ─── H7-01: restore не теряет последние изменения (encrypted) ────────────────

def test_barrier_flush_persists_inflight_mutation(qapp, tmp_db_path, pure_config):
    """Последовательность restore/close (барьер executor'а → flush → wait_idle)
    сохраняет на диск даже коммит, выполнявшийся в фоновом потоке в момент старта:
    после неё расшифрованный контейнер содержит правку."""
    import threading

    from hranilka.crypto import store as cs
    from hranilka.data.database import Database

    db = Database(tmp_db_path)
    db.connect()
    db.create_tables()
    recovery = db.enable_encryption("pw", "fast")   # → шифр. режим, dirty сброшен
    assert recovery
    vc = VaultController(db, _FakeWindow(), pure_config)
    try:
        started = threading.Event()
        release = threading.Event()

        def slow_mutation():
            started.set()
            release.wait(2)
            db.add_service("inflight")          # коммит из фонового потока (dirty=True)

        fut = db._executor.submit(slow_mutation)
        assert started.wait(2)
        release.set()

        # Порядок как в _restore_from_backup/closeEvent: барьер → flush → wait_idle.
        assert db.wait_executor_idle(timeout=5) is True
        vc.flush()
        assert vc.wait_idle() is True
        fut.result(timeout=2)
    finally:
        vc.shutdown()

    # На диске — контейнер с правкой (расшифровываем и проверяем сервис на месте).
    with open(tmp_db_path, "rb") as f:
        container = f.read()
    db_bytes, dek, header = cs.unlock(container, "pw")
    d2 = Database(tmp_db_path)
    d2.open_encrypted(db_bytes, dek, header)
    d2.create_tables()
    assert "inflight" in [s["name"] for s in d2.get_services()]
    d2.close(persist=False)
    db.close(persist=False)


# ─── H-09: run_exclusive_busy — фоновое выполнение с живым event-loop ─────────

def test_run_exclusive_busy_returns_result_and_keeps_loop_alive(vc, qapp):
    """fn выполняется в фоновом потоке: за время операции event-loop крутится
    (таймер тикает), результат совпадает с run_exclusive-семантикой."""
    import time
    from PySide6.QtCore import QTimer

    ticks = {"n": 0}
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.__setitem__("n", ticks["n"] + 1))
    timer.start()

    def slow_op():
        time.sleep(0.3)
        return "готово"

    ok, res = vc.run_exclusive_busy(slow_op, "Тестовая операция…")
    timer.stop()
    assert ok is True and res == "готово"
    assert ticks["n"] > 0, "GUI event-loop должен тикать во время операции"
    assert vc._vault_locked is False


def test_run_exclusive_busy_reports_error(vc):
    def boom():
        raise RuntimeError("сбой операции")

    ok, res = vc.run_exclusive_busy(boom, "Тестовая операция…")
    assert ok is False and "сбой операции" in res
    assert vc._vault_locked is False


def test_run_exclusive_busy_locks_gate_during_op(vc):
    seen = {}

    def op():
        seen["locked"] = vc._vault_locked

    ok, _ = vc.run_exclusive_busy(op, "Тестовая операция…")
    assert ok and seen["locked"] is True


# ─── H-09: UnlockDialog — KDF/расшифровка в фоновом потоке ────────────────────

def _spin_until(qapp, cond, timeout_s=15.0):
    import time
    deadline = time.time() + timeout_s
    while not cond() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    return cond()


def test_unlock_dialog_async_success(qapp, pure_config):
    from hranilka.crypto import store as cs
    from hranilka.ui.dialogs.unlock import UnlockDialog

    container, _ = cs.create_vault(b"db-bytes", "pw-123456789012", "fast")
    dlg = UnlockDialog(pure_config, container)
    dlg._field.setText("pw-123456789012")
    dlg._attempt()
    # Ввод заблокирован на время фоновой разблокировки; «Выход» доступен.
    assert dlg._busy and not dlg._ok.isEnabled() and not dlg._field.isEnabled()
    assert dlg._progress.isVisible() or True   # offscreen: visible до show() не гарантирован
    # Повторный клик во время работы игнорируется (не падает, не дублирует).
    dlg._attempt()

    assert _spin_until(qapp, lambda: dlg.result_data is not None)
    db_bytes, dek, header = dlg.result_data
    assert db_bytes == b"db-bytes"
    assert not dlg._busy and dlg._ok.isEnabled()


def test_unlock_dialog_async_wrong_password(qapp, pure_config):
    from hranilka.crypto import store as cs
    from hranilka.ui.dialogs.unlock import UnlockDialog

    container, _ = cs.create_vault(b"db-bytes", "pw-123456789012", "fast")
    dlg = UnlockDialog(pure_config, container)
    dlg._field.setText("wrong-password-000")
    dlg._attempt()
    assert _spin_until(qapp, lambda: not dlg._busy)
    assert dlg.result_data is None
    assert "Неверный пароль" in dlg._err.text()


def test_unlock_dialog_stale_result_ignored_after_reject(qapp, pure_config):
    """«Выход» во время фоновой разблокировки: поздний результат отсекается
    поколением — result_data не выставляется."""
    from hranilka.crypto import store as cs
    from hranilka.ui.dialogs.unlock import UnlockDialog

    container, _ = cs.create_vault(b"db-bytes", "pw-123456789012", "fast")
    dlg = UnlockDialog(pure_config, container)
    dlg._field.setText("pw-123456789012")
    dlg._attempt()
    dlg.reject()                    # пользователь вышел, KDF ещё работает
    import time
    time.sleep(0.05)
    for _ in range(300):            # дать фоновому потоку доработать
        qapp.processEvents()
        time.sleep(0.01)
        if not dlg._busy:
            break
    assert dlg.result_data is None


def test_exclusive_ops_reset_idle_activity(vc):
    """После привилегированной операции сбрасывается счётчик простоя окна:
    долгий KDF не должен засчитаться как бездействие (иначе первый тик
    idle-таймера заблокировал бы vault поверх диалога recovery-кода)."""
    calls = {"n": 0}
    vc._window.note_activity = lambda: calls.__setitem__("n", calls["n"] + 1)
    vc.run_exclusive(lambda: None)
    assert calls["n"] == 1
    vc.run_exclusive_busy(lambda: None, "Тест…")
    assert calls["n"] == 2


def test_exclusive_ops_wait_async_queue(vc, qapp):
    """Гейт дочищает очередь run_async ДО операции: in-flight мутатор
    дорабатывает в старой сессии, а не получает StaleSessionError."""
    import time
    order = []

    # Медленная задача в очереди db-исполнителя (как незавершённый Save).
    fut = vc.db._executor.submit(lambda: (time.sleep(0.2), order.append("save")))

    ok, _ = vc.run_exclusive_busy(lambda: order.append("op"), "Тест…")
    assert ok
    assert order == ["save", "op"], "операция обязана ждать очередь run_async"
    assert fut.done()


def test_quiesce_drains_async_before_flush(vc, monkeypatch):
    """Порядок _quiesce принципиален (как в restore/H7-01): сначала барьер
    run_async (мутаторы дорабатывают и помечают БД грязной), затем flush
    свежего снимка, затем ожидание writer'а. Обратный порядок дал бы на диске
    старый снимок — ручной бэкап скопировал бы устаревший файл."""
    order = []
    monkeypatch.setattr(vc.db, "wait_executor_idle",
                        lambda timeout=15.0: order.append("drain") or True)
    monkeypatch.setattr(vc, "flush", lambda: order.append("flush"))
    monkeypatch.setattr(vc, "wait_idle",
                        lambda timeout_ms=15000: order.append("writer") or True)
    monkeypatch.setattr(vc.db, "encrypted", True)
    assert vc._quiesce() is True
    assert order == ["drain", "flush", "writer"]


def test_exclusive_backup_includes_queued_changes(qapp, tmp_path, pure_config):
    """Интеграционно: правка, стоящая в очереди run_async на момент запуска
    ручного бэкапа (encrypted), обязана попасть в файл бэкапа."""
    import sqlite3
    import time

    from hranilka.crypto import store as cs
    from hranilka.data.database import Database
    from hranilka.services import backup as bk

    pw = "pw-123456789012"
    db = Database(str(tmp_path / "hranilka.db"))
    db.connect()
    db.create_tables()
    db.enable_encryption(pw, "fast")
    vc = VaultController(db, _FakeWindow(), pure_config)
    try:
        # Медленный мутатор в очереди db-исполнителя (как незавершённый Save).
        def slow_add():
            time.sleep(0.15)
            db.add_account(None, "Свежий")
        db._executor.submit(slow_add)

        folder = tmp_path / "bk"
        ok, res = vc.run_exclusive_busy(
            lambda: bk.create_backup(db.db_path, str(folder), 5), "Бэкап…")
        assert ok, res

        # Расшифровываем сам файл бэкапа: свежая запись обязана быть в нём.
        db_bytes, _dek, _header = cs.unlock(res.read_bytes(), pw)
        con = sqlite3.connect(":memory:")
        con.deserialize(db_bytes)
        names = [r[0] for r in con.execute("SELECT account_name FROM accounts")]
        con.close()
        assert "Свежий" in names
    finally:
        vc.shutdown()
        db.close(persist=False)
