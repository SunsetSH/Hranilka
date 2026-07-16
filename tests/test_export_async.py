"""M-04 (docs/CODE_REVIEW_VPS_SERVERS_2026-07-15.md): снимок и формирование
файла экспорта вынесены из GUI-потока.

Три области:
  1. Фильтрация bulk-снимка (bulk.py:export_subtree) — отключённые разделы
     (галерея / фин / серверы) не читаются из БД вовсе, не просто
     отфильтровываются постфактум. Проверяется через sqlite3.Connection.
     set_trace_callback — единственный надёжный способ перехватить SQL
     sqlite3.Cursor: C-расширение, execute на инстансе не переопределить
     (AttributeError: attribute is read-only).
  2. Атомарная публикация (services/export.py:write_atomic) — временный файл
     переименовывается только при успехе; при исключении форматтера целевой
     файл не создаётся/не повреждается.
  3. Смоук асинхронного пути ExportDialog (util.fire без работающего
     qasync-loop выполняет корутину синхронно — тот же паттерн, что и в
     остальных тестах проекта, см. test_fin_options.py/test_server_options.py).
"""
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog

from hranilka.data.database import StaleSessionError
from hranilka.services import export
from hranilka.services.export import Options
from hranilka.ui.dialogs import export_dialog as ed_mod
from hranilka.ui.dialogs.export_dialog import ExportDialog


def _theme():
    return {"font": "Consolas", "font_size": 14, "text_color": "#000000",
            "main_bg_color": "#F0F0F0", "tree_bg_color": "#FFFFFF"}


# ─── 1. Фильтрация bulk-снимка ────────────────────────────────────────────────

def _seed(db):
    """Сервис с аккаунтом+картинкой, фин-записью+картинкой, сервером+картинкой —
    по одному BLOB на каждый из трёх независимых разделов."""
    sid = db.add_service("S")
    aid = db.add_account(sid, "Акк")
    db.cursor.execute(
        "INSERT INTO gallery (account_id, description, image_data) VALUES (?, 'a', ?)",
        (aid, b"\x89PNGfakeaccount"))
    fid = db.add_fin_item(sid, "bank_card", "Карта")
    db.cursor.execute(
        "INSERT INTO fin_gallery (item_id, description, image_data) VALUES (?, 'f', ?)",
        (fid, b"\x89PNGfakefin"))
    srvid = db.add_server(sid, "VPS")
    db.cursor.execute(
        "INSERT INTO server_gallery (server_id, description, image_data) VALUES (?, 's', ?)",
        (srvid, b"\x89PNGfakeserver"))
    db._commit()
    return sid, aid, fid, srvid


def _traced(db):
    """Список SQL, выполненных на db.conn (для проверки «вовсе не читалось»)."""
    calls = []
    db.conn.set_trace_callback(lambda sql: calls.append(sql))
    return calls


def test_gallery_blobs_not_read_when_include_gallery_false(db):
    _seed(db)
    calls = _traced(db)
    tree = db.export_subtree(include_gallery=False)
    assert not any("FROM gallery WHERE account_id IN" in c for c in calls)
    assert not any("FROM fin_gallery WHERE item_id IN" in c for c in calls)
    assert not any("FROM server_gallery WHERE server_id IN" in c for c in calls)

    service = next(n for n in tree if n["type"] == "service")
    account = next(n for n in service["children"] if n["type"] == "account")
    assert account["card"]["gallery"] == []
    fin_leaf = next(n for n in service["children"] if n["type"] == "card")
    assert fin_leaf["fin"]["gallery"] == []
    server_leaf = next(n for n in service["children"] if n["type"] == "server")
    assert server_leaf["server"]["gallery"] == []


def test_fin_not_read_when_include_fin_false(db):
    _seed(db)
    calls = _traced(db)
    tree = db.export_subtree(include_fin=False)
    assert not any("FROM fin_items WHERE id IN" in c for c in calls)
    assert not any("FROM fin_gallery WHERE item_id IN" in c for c in calls)
    assert not any("FROM fin_links WHERE item_id IN" in c for c in calls)

    service = next(n for n in tree if n["type"] == "service")
    fin_leaf = next(n for n in service["children"] if n["type"] == "card")
    assert "fin" not in fin_leaf                 # карточка вовсе не подгружена


def test_servers_not_read_when_include_servers_false(db):
    _seed(db)
    calls = _traced(db)
    tree = db.export_subtree(include_servers=False)
    assert not any("FROM servers WHERE id IN" in c for c in calls)
    assert not any("FROM server_gallery WHERE server_id IN" in c for c in calls)
    assert not any("FROM server_links WHERE server_id IN" in c for c in calls)

    service = next(n for n in tree if n["type"] == "service")
    server_leaf = next(n for n in service["children"] if n["type"] == "server")
    assert "server" not in server_leaf            # карточка вовсе не подгружена


def test_default_flags_keep_old_behavior(db):
    """Обратная совместимость: вызовы без keyword-флагов (как в остальных
    тестах экспорта) по-прежнему читают всё — include_*=True по умолчанию."""
    _seed(db)
    tree = db.export_subtree()
    service = next(n for n in tree if n["type"] == "service")
    account = next(n for n in service["children"] if n["type"] == "account")
    assert len(account["card"]["gallery"]) == 1
    assert account["card"]["gallery"][0]["data"] is not None
    fin_leaf = next(n for n in service["children"] if n["type"] == "card")
    assert len(fin_leaf["fin"]["gallery"]) == 1
    server_leaf = next(n for n in service["children"] if n["type"] == "server")
    assert len(server_leaf["server"]["gallery"]) == 1


# ─── 2. Атомарная публикация (export.write_atomic) ───────────────────────────

def test_write_atomic_publishes_via_rename(tmp_path):
    target = tmp_path / "out.txt"

    def fake_func(tree, opts, path):
        Path(path).write_text("hello")

    export.write_atomic(fake_func, [], object(), str(target))
    assert target.read_text() == "hello"
    assert list(tmp_path.glob("*.tmp*")) == []        # временный файл убран


def test_write_atomic_failure_leaves_target_untouched(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("original")

    def failing_func(tree, opts, path):
        Path(path).write_text("partial")              # временный файл частично написан
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        export.write_atomic(failing_func, [], object(), str(target))
    assert target.read_text() == "original"           # цель не повреждена/не подменена
    assert list(tmp_path.glob("*.tmp*")) == []          # временный файл подчищен


def test_write_atomic_wipes_tmp_on_failure(tmp_path, monkeypatch):
    """M-02: обрыв форматтера — временный plaintext-файл затирается через
    best_effort_wipe, а не голым unlink (иначе частичный экспорт с секретами
    мог остаться читаемым в свободных блоках ФС). Проверяем, что модуль
    зовёт именно best_effort_wipe (как в тестах pre-migrate/restore, M-02) —
    после затирания файла на диске нет, тот же признак, что и у прежних тестов
    write_atomic (*.tmp* пусто)."""
    target = tmp_path / "out.txt"
    wiped_paths = []

    def spy_wipe(path):
        wiped_paths.append(path)
        Path(path).unlink()

    monkeypatch.setattr(export, "best_effort_wipe", spy_wipe)

    def failing_func(tree, opts, path):
        Path(path).write_text("partial-secret-data")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        export.write_atomic(failing_func, [], object(), str(target))

    assert len(wiped_paths) == 1                        # затирание вызвано ровно раз
    assert Path(wiped_paths[0]).parent == tmp_path
    assert list(tmp_path.glob("*.tmp*")) == []           # временный файл убран


def test_write_atomic_no_target_when_formatter_never_ran(tmp_path):
    """Форматтер упал до создания временного файла — целевой файл не появляется."""
    target = tmp_path / "out.txt"

    def failing_func(tree, opts, path):
        raise RuntimeError("boom before write")

    with pytest.raises(RuntimeError):
        export.write_atomic(failing_func, [], object(), str(target))
    assert not target.exists()
    assert list(tmp_path.glob("*.tmp*")) == []


# ─── 1b. Потоковый HTML (M-04, второй проход ревью) ───────────────────────────
# Снимок с gallery_ids_only=True не содержит BLOB — только id строки галереи;
# байты читаются по одной картинке через image_provider(kind, image_id),
# передаваемый export_html/write_atomic (см. export_dialog._make_image_provider
# для боевого моста через db.run_async).

def _seed_one_image(db, desc="подпись", data=b"\x89PNGfakeaccount"):
    sid = db.add_service("S")
    aid = db.add_account(sid, "Акк")
    db.cursor.execute(
        "INSERT INTO gallery (account_id, description, image_data) VALUES (?, ?, ?)",
        (aid, desc, data))
    db._commit()
    return sid, aid


def _find_account(tree):
    """Аккаунт вложен в дерево под service (export_subtree сохраняет
    иерархию папка/сервис/аккаунт), а не лежит на верхнем уровне."""
    for n in tree:
        if n["type"] == "account":
            return n
        found = _find_account(n.get("children", []))
        if found is not None:
            return found
    return None


def test_gallery_ids_only_snapshot_has_no_blob(db):
    _seed_one_image(db)
    tree = db.export_subtree(gallery_ids_only=True)
    account = _find_account(tree)
    row = account["card"]["gallery"][0]
    assert row["data"] is None                 # BLOB не читался в снимок
    assert row["image_id"] is not None          # id строки — есть, чем дочитать


def test_stream_html_matches_full_blob_render(db, tmp_path):
    """HTML, собранный по потоковому снимку (без BLOB + provider), побайтово
    совпадает с прежним режимом (снимок сразу со всеми BLOB, без provider)."""
    _seed_one_image(db)
    opts = Options(theme=_theme(), title="Т")

    full_path = tmp_path / "full.html"
    export.export_html(db.export_subtree(), opts, str(full_path))
    full_html = full_path.read_text(encoding="utf-8")
    assert "подпись" in full_html and "base64," in full_html

    stream_path = tmp_path / "stream.html"
    tree = db.export_subtree(gallery_ids_only=True)
    export.export_html(tree, opts, str(stream_path),
                       image_provider=lambda kind, iid: db.load_gallery_image(iid))

    assert stream_path.read_text(encoding="utf-8") == full_html


def test_stream_html_provider_called_once_per_image(db, tmp_path):
    sid = db.add_service("S")
    aid = db.add_account(sid, "Акк")
    for i in range(3):
        db.cursor.execute(
            "INSERT INTO gallery (account_id, description, image_data) VALUES (?, ?, ?)",
            (aid, f"d{i}", (f"img{i}".encode()) * 50))
    db._commit()

    calls = []

    def provider(kind, image_id):
        calls.append((kind, image_id))
        return db.load_gallery_image(image_id)

    tree = db.export_subtree(gallery_ids_only=True)
    out = tmp_path / "e.html"
    export.export_html(tree, Options(theme=_theme(), title="Т"), str(out),
                       image_provider=provider)

    assert len(calls) == 3
    assert len(set(calls)) == 3                 # по разу на картинку, не повтор
    # Снимок остаётся без байт даже после записи файла — provider не подмешал
    # результат обратно в дерево (никакого накопления).
    account = _find_account(tree)
    assert all(g["data"] is None for g in account["card"]["gallery"])


def test_stream_html_provider_failure_leaves_target_untouched(db, tmp_path):
    """Исключение провайдера — как и любой сбой форматтера — не оставляет
    частично записанный целевой файл (write_atomic, атомарность сохранена)."""
    _seed_one_image(db)
    tree = db.export_subtree(gallery_ids_only=True)

    def failing_provider(kind, image_id):
        raise RuntimeError("boom")

    target = tmp_path / "out.html"
    with pytest.raises(RuntimeError):
        export.write_atomic(export.export_html, tree, Options(theme=_theme(), title="Т"),
                            str(target), image_provider=failing_provider)
    assert not target.exists()
    assert list(tmp_path.glob("*.tmp*")) == []


def test_export_dialog_html_gallery_streams_via_run_async_bridge(
        qapp, pure_config, db, tmp_path, monkeypatch):
    """Экспорт HTML+галерея целиком через диалог (боевой мост
    _make_image_provider: run_coroutine_threadsafe -> db.run_async из потока
    форматирования) — файл создаётся, картинка попадает в HTML как base64."""
    _seed_one_image(db, desc="капча")

    dlg = ExportDialog(pure_config, db, None, None, "Т", None)
    # html выбран по умолчанию (первая радиокнопка), галерея включена по
    # умолчанию — формат/чекбоксы трогать не нужно.
    out = tmp_path / "e.html"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    monkeypatch.setattr(ed_mod, "themed_info", lambda *a, **k: None)

    dlg._do_export()
    assert dlg.result() == QDialog.Accepted
    html = out.read_text(encoding="utf-8")
    assert "капча" in html
    assert "base64," in html
    dlg.deleteLater()


# ─── 3. Смоук асинхронного пути ExportDialog ──────────────────────────────────
# util.fire без работающего qasync-loop выполняет корутину синхронно — тот же
# паттерн, что и в остальных тестах проекта (test_fin_options.py и др.).

def test_export_dialog_full_flow_writes_file(qapp, pure_config, db, tmp_path,
                                              monkeypatch):
    sid = db.add_service("S")
    db.add_account(sid, "Акк", login="user", password="pass")

    dlg = ExportDialog(pure_config, db, None, None, "Т", None)
    for btn in dlg._fmt_btns.buttons():
        if btn.property("fmt") == "txt":
            btn.setChecked(True)
    out = tmp_path / "e.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    # themed_info(«Готово») зовёт d.exec() — модальный QMessageBox повесил бы
    # headless-тест; тут важно только успешное завершение фона, не сам попап.
    monkeypatch.setattr(ed_mod, "themed_info", lambda *a, **k: None)

    assert not dlg._busy
    dlg._do_export()
    assert not dlg._busy                       # снято после завершения фона
    assert dlg.result() == QDialog.Accepted     # диалог закрылся по успеху
    assert out.exists()
    assert "Акк" in out.read_text(encoding="utf-8")
    dlg.deleteLater()


def test_export_dialog_busy_guard_blocks_reentry(qapp, pure_config, db, monkeypatch):
    """Повторный клик «Экспортировать…» пока идёт фон — не допускается (M-04):
    _do_export выходит немедленно, даже не открывая диалог выбора пути."""
    dlg = ExportDialog(pure_config, db, None, None, "Т", None)
    dlg._busy = True

    def _boom(*a, **k):
        raise AssertionError("QFileDialog не должен вызываться при busy")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(_boom))

    dlg._do_export()                            # тихий no-op
    dlg.deleteLater()


def test_export_dialog_stale_session_aborts_without_writing(qapp, pure_config, db,
                                                             tmp_path, monkeypatch):
    """StaleSessionError во время фонового экспорта — тихий обрыв без записи
    файла (M-04, п.7), диалог не закрывается сообщением об успехе."""
    sid = db.add_service("S")
    db.add_account(sid, "Акк")

    dlg = ExportDialog(pure_config, db, None, None, "Т", None)
    out = tmp_path / "e.html"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))

    async def _raise_stale(method, *a, **k):
        raise StaleSessionError()
    monkeypatch.setattr(db, "run_async", _raise_stale)

    dlg._do_export()
    assert not dlg._busy
    assert dlg.result() != QDialog.Accepted
    assert not out.exists()
    dlg.deleteLater()


def test_export_dialog_empty_tree_no_file_written(qapp, pure_config, db, tmp_path,
                                                   monkeypatch):
    """Пустая база (снимок посчитан уже в фоне) — сообщение, файл не создаётся."""
    dlg = ExportDialog(pure_config, db, None, None, "Т", None)
    out = tmp_path / "e.html"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    msgs = []
    monkeypatch.setattr(
        ed_mod, "themed_info",
        lambda cfg, parent, title, text: msgs.append(title))

    dlg._do_export()
    assert not out.exists()
    assert dlg.result() != QDialog.Accepted
    assert msgs == ["Экспорт"]
    dlg.deleteLater()


def test_set_controls_enabled_restores_derived_states(qapp, pure_config, db):
    """Повторное включение элементов управления после busy пересчитывает
    производные состояния (секреты зависят от fin/servers, а не включаются
    безусловно)."""
    dlg = ExportDialog(pure_config, db, None, None, "Т", None,
                       show_fin=True, show_servers=True)
    dlg._chk_fin.setChecked(False)
    dlg._chk_servers.setChecked(False)
    assert not dlg._chk_fin_secrets.isEnabled()
    assert not dlg._chk_server_secrets.isEnabled()

    dlg._set_controls_enabled(False)
    for w in (dlg._ok, dlg._cancel_btn, dlg._chk_basic, dlg._chk_fin, dlg._chk_servers):
        assert not w.isEnabled()

    dlg._set_controls_enabled(True)
    assert dlg._ok.isEnabled()
    assert not dlg._chk_fin_secrets.isEnabled()      # разделы сняты — не включились
    assert not dlg._chk_server_secrets.isEnabled()
    dlg.deleteLater()
