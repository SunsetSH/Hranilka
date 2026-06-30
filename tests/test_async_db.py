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

from database import Database


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
