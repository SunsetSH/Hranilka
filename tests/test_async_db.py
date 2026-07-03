"""Асинхронный доступ к БД (Часть 2 рефакторинга: asyncio + поток-исполнитель).

Проверяем, что:
  * run_async выполняет метод в ФОНОВОМ потоке (UI-поток не блокируется);
  * синхронные и асинхронные вызовы сериализуются без порчи данных (RLock +
    единственный воркер);
  * вложенные вызовы между методами в фоновом потоке не дают дедлок (RLock
    реентрантный);
  * чтение BLOB (галерея) корректно работает из фонового потока — на этом
    держится фоновый предпросмотр картинок.
"""
import asyncio
import sqlite3
import threading

import pytest

from database import Database, StaleSessionError


@pytest.fixture
def adb(tmp_db_path):
    d = Database(tmp_db_path)
    d.connect()
    d.create_tables()
    yield d
    d.shutdown_executor()
    try:
        d.close(persist=False)
    except Exception:
        pass


async def test_run_async_executes_off_caller_thread(adb):
    caller = threading.get_ident()
    seen = {}

    def probe():
        seen["thread"] = threading.get_ident()
        # вложенный вызов обёрнутого метода в том же фоновом потоке — не дедлок
        return adb.add_service("S")

    sid = await adb.run_async(probe)
    assert seen["thread"] != caller          # выполнено в фоновом потоке
    assert sid == 1


async def test_async_method_matches_sync(adb):
    sid = adb.add_service("S")
    aid = adb.add_account(sid, "Акк")
    card = await adb.run_async(adb.load_account, aid)
    assert card["fields"]["account_name"] == "Акк"


async def test_concurrent_async_inserts_no_corruption(adb):
    sid = adb.add_service("S")

    async def add(n):
        return await adb.run_async(adb.add_account, sid, f"A{n}")

    ids = await asyncio.gather(*[add(i) for i in range(25)])
    assert len(set(ids)) == 25                # все id уникальны, ничего не затёрто
    accounts = await adb.run_async(adb.get_all_accounts)
    assert len(accounts) == 25


async def test_blob_read_from_worker_thread(adb):
    """load_gallery_image потокобезопасен (под RLock) — так фоновый воркер
    предпросмотра читает BLOB вне UI-потока."""
    sid = adb.add_service("S")
    aid = adb.add_account(sid, "A")
    payload = b"\x07" * 4096
    adb.cursor.execute(
        "INSERT INTO gallery (account_id, description, image_data) VALUES (?, ?, ?)",
        (aid, "d", sqlite3.Binary(payload)))
    adb._commit()
    img_id = adb.cursor.lastrowid

    data = await adb.run_async(adb.load_gallery_image, img_id)
    assert data == payload


async def test_get_links_reentrant_under_async(adb):
    """get_links внутри вызывает get_account_path (обёрнутый метод) — реентрантный
    RLock не должен давать дедлок при выполнении в фоновом потоке."""
    sid = adb.add_service("S")
    a = adb.add_account(sid, "A")
    b = adb.add_account(sid, "B")
    adb.set_links(a, [b])
    links = await adb.run_async(adb.get_links, a)
    assert [l["id"] for l in links] == [b]


def test_save_account_with_links_atomic(adb):
    """Карточка и связи сохраняются вместе (H6-02)."""
    sid = adb.add_service("S")
    a = adb.add_account(sid, "A")
    b = adb.add_account(sid, "B")
    card = adb.load_account(a)
    card["fields"]["account_name"] = "A-new"
    adb.save_account_with_links(a, card, [b])
    assert adb.load_account(a)["fields"]["account_name"] == "A-new"
    assert [l["id"] for l in adb.get_links(a)] == [b]


def test_save_account_with_links_rolls_back_on_error(adb, monkeypatch):
    """Сбой записи связей откатывает и запись карточки — обе в одной транзакции
    (H6-02): раньше это были две транзакции, и карточка оставалась записанной."""
    sid = adb.add_service("S")
    a = adb.add_account(sid, "A")
    b = adb.add_account(sid, "B")
    card = adb.load_account(a)
    card["fields"]["account_name"] = "SHOULD-NOT-PERSIST"

    def boom(*args, **kwargs):
        raise RuntimeError("links failed")

    monkeypatch.setattr(adb, "_set_links_rows", boom)
    with pytest.raises(RuntimeError):
        adb.save_account_with_links(a, card, [b])
    assert adb.load_account(a)["fields"]["account_name"] == "A"


async def test_queued_async_aborts_after_session_change(adb):
    """Задача run_async, поставленная в очередь до смены сессии БД (close/lock/
    restore), НЕ выполняется над новым соединением, а поднимает StaleSessionError
    (H6-03). Иначе данные прежней сессии могли бы попасть в восстановленную базу."""
    # Детерминированная синхронизация без sleep (M-15): blocker сигналит started,
    # как только занял единственный воркер, и держит его до release. Тест ждёт
    # started через run_in_executor (тот же executor — задача выполнится ПОСЛЕ
    # blocker'а, значит queued уже стоит в очереди за blocker'ом с прежним
    # поколением), затем меняет сессию и отпускает воркер.
    started = threading.Event()
    release = threading.Event()

    def blocker():
        started.set()                       # воркер занят blocker'ом
        release.wait(2)                     # держим воркер до сигнала теста

    loop = asyncio.get_running_loop()
    busy = asyncio.ensure_future(adb.run_async(blocker))
    await loop.run_in_executor(None, started.wait, 2)   # ждём, пока blocker занял воркер
    queued = asyncio.ensure_future(adb.run_async(adb.get_all_accounts))
    # Уступаем циклу, пока корутина queued не поставит свою задачу в очередь
    # единственного воркера БД (за blocker'ом). Проверяем это детерминированно по
    # размеру рабочей очереди executor'а — без таймингов/sleep фиксированной длины.
    while adb._executor._work_queue.qsize() < 1:
        await asyncio.sleep(0)
    # Теперь queued точно стоит в очереди за blocker'ом с прежним поколением.
    adb._bump_session()                     # сессия сменилась, пока queued ждёт
    release.set()                           # отпускаем воркер → queued берётся за работу

    with pytest.raises(StaleSessionError):
        await queued
    await busy


# ─── H7-01: барьер executor'а (wait_executor_idle) ───────────────────────────

def test_wait_executor_idle_true_when_empty(adb):
    """Пустой executor: барьер сразу True."""
    assert adb.wait_executor_idle(timeout=2) is True


def test_wait_executor_idle_waits_for_inflight(adb):
    """Барьер дожидается выполняющейся задачи: она успевает записать данные ДО
    возврата True (иначе restore/close потеряли бы последний коммит)."""
    started = threading.Event()
    release = threading.Event()
    done = {}

    def slow():
        started.set()
        release.wait(2)
        adb.add_service("late")             # «поздний» коммит из фонового потока
        done["ok"] = True

    fut = adb._executor.submit(slow)
    assert started.wait(2)
    release.set()
    # Барьер сабмитит no-op ПОСЛЕ slow (FIFO, один воркер) → ждёт его завершения.
    assert adb.wait_executor_idle(timeout=5) is True
    assert done.get("ok") is True
    assert [s["name"] for s in adb.get_services()] == ["late"]
    fut.result(timeout=2)


def test_wait_executor_idle_timeout_returns_false(adb):
    """Если задача не отпускает воркера дольше timeout — барьер возвращает False
    (вызыватель обязан прервать restore/close)."""
    started = threading.Event()
    release = threading.Event()

    def blocker():
        started.set()
        release.wait(5)

    fut = adb._executor.submit(blocker)
    assert started.wait(2)
    assert adb.wait_executor_idle(timeout=0.2) is False
    release.set()
    fut.result(timeout=5)
