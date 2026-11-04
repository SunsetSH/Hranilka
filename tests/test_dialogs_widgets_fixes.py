"""Регрессии правок диалогов и виджетов: пароль с пробелами по краям, галерея,
seed-фраза, секретные вопросы, срок карты, дата, корзина, экспорт, настройки."""
import json

import pytest
from PySide6.QtCore import QMimeData
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from hranilka.crypto import store as cs
from hranilka.ui.dialogs import recycle_bin as rb_mod
from hranilka.ui.dialogs.export_dialog import ExportDialog
from hranilka.ui.dialogs.recycle_bin import RecycleBinDialog
from hranilka.ui.dialogs.settings.dialog import SettingsDialog
from hranilka.ui.dialogs.unlock import UnlockDialog, unlock_with_fallback
from hranilka.ui.widgets import gallery as gal
from hranilka.ui.widgets import gallery_viewer
from hranilka.ui.widgets.fields import CopyableDateField
from hranilka.ui.widgets.fin_fields import ExpiryField, normalize_expiry
from hranilka.ui.widgets.gallery import GalleryWidget
from hranilka.ui.widgets.secret_questions import SecretQuestionsWidget
from hranilka.ui.widgets.seed_phrase import SeedPhraseWidget


# --- 1. пароль с пробелами по краям -----------------------------------------

def test_unlock_exact_text_first_then_stripped(monkeypatch):
    calls = []

    def fake_unlock(_c, secret, is_recovery=False):
        calls.append(secret)
        if secret != "pw":
            raise cs.WrongPassword()
        return "ok"

    monkeypatch.setattr(cs, "unlock", fake_unlock)
    assert unlock_with_fallback(b"", " pw ", False) == "ok"
    assert calls == [" pw ", "pw"]


def test_unlock_exact_password_with_spaces_unlocks_once(monkeypatch):
    calls = []

    def fake_unlock(_c, secret, is_recovery=False):
        calls.append(secret)
        return "ok"

    monkeypatch.setattr(cs, "unlock", fake_unlock)
    assert unlock_with_fallback(b"", " pw ", False) == "ok"
    assert calls == [" pw "]


def test_unlock_no_retry_without_edge_whitespace_or_for_recovery(monkeypatch):
    calls = []

    def always_wrong(_c, secret, is_recovery=False):
        calls.append(secret)
        raise cs.WrongPassword()

    monkeypatch.setattr(cs, "unlock", always_wrong)
    with pytest.raises(cs.WrongPassword):
        unlock_with_fallback(b"", "pw", False)
    with pytest.raises(cs.WrongPassword):
        unlock_with_fallback(b"", " CODE ", True)
    assert calls == ["pw", " CODE "]


def test_unlock_dialog_paste_keeps_spaces_clears_only_after_success(qapp, pure_config):
    dlg = UnlockDialog(pure_config, b"x")
    clip = QApplication.clipboard()
    clip.setText("  secret  ")
    dlg._paste()
    assert dlg._field.text() == "  secret  "
    assert clip.text() == "  secret  "          # опечатка: можно вставить снова
    dlg._on_unlock_done(dlg._gen, None, cs.WrongPassword())
    assert clip.text() == "  secret  "          # неудача — буфер не трогаем
    dlg._on_unlock_done(dlg._gen, "res", None)
    assert clip.text() == ""


def test_unlock_dialog_success_keeps_foreign_clipboard(qapp, pure_config):
    dlg = UnlockDialog(pure_config, b"x")
    clip = QApplication.clipboard()
    clip.setText("secret")
    dlg._paste()
    clip.setText("something else")
    dlg._on_unlock_done(dlg._gen, "res", None)
    assert clip.text() == "something else"


def test_unlock_dialog_sends_unstripped_password(qapp, pure_config, monkeypatch):
    seen = []

    def fake_unlock(_c, secret, is_recovery=False):
        seen.append(secret)
        return (b"db", b"k", {})

    monkeypatch.setattr(cs, "unlock", fake_unlock)
    dlg = UnlockDialog(pure_config, b"x")
    dlg._field.setText(" pw ")
    dlg._attempt()
    for _ in range(500):
        if dlg.result_data is not None:
            break
        QTest.qWait(10)
    assert dlg.result_data is not None
    assert seen == [" pw "]


# --- 2. галерея: удалённая заглушка не воскресает -----------------------------

def _png() -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    img = QImage(10, 10, QImage.Format_RGB32)
    img.fill(0x3366CC)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


@pytest.mark.parametrize("switch_card", [False, True])
async def test_deleted_placeholder_is_not_saved_as_orphan(
        qapp, monkeypatch, switch_card):
    monkeypatch.setattr(gal, "_warn", lambda *a, **k: None)
    monkeypatch.setattr(gal, "_confirm", lambda *a, **k: True)
    data = _png()
    monkeypatch.setattr(gal, "_read_file", lambda p, mx: data)
    monkeypatch.setattr(gal, "_prepare_image_bytes",
                        lambda d, downscale: (d, (10, 10), None))
    gw = GalleryWidget()
    orphans = []
    gw.set_orphan_upload_handler(lambda aid, desc, d: orphans.append(aid))
    gw.add_item(None)
    item = gw.items[-1]
    gen = gw._data_gen
    gw.remove_item(item["widget"])
    assert item["cancelled"] is True
    if switch_card:
        gw._data_gen += 1
    await gw._upload_pipeline(item, "x.png", gen, account_id=7)
    assert orphans == []
    assert item["bytes"] is None


async def test_card_switch_still_saves_orphan(qapp, monkeypatch):
    monkeypatch.setattr(gal, "_warn", lambda *a, **k: None)
    data = _png()
    monkeypatch.setattr(gal, "_read_file", lambda p, mx: data)
    monkeypatch.setattr(gal, "_prepare_image_bytes",
                        lambda d, downscale: (d, (10, 10), None))
    gw = GalleryWidget()
    orphans = []
    gw.set_orphan_upload_handler(lambda aid, desc, d: orphans.append(aid))
    gw.add_item(None)
    item = gw.items[-1]
    gen = gw._data_gen
    gw._data_gen += 1
    await gw._upload_pipeline(item, "x.png", gen, account_id=7)
    assert orphans == [7]


# --- 3. recovery-код показывается даже при сбое сохранения конфига -----------

def test_enc_enable_shows_recovery_when_config_save_fails(
        qapp, pure_config, monkeypatch):
    dlg = SettingsDialog(pure_config)
    shown, infos = [], []

    class FakeDb:
        encrypted = False

        def enable_encryption(self, pw, preset):
            return "REC-CODE"

    dlg._db = FakeDb()
    monkeypatch.setattr(dlg, "_ask_new_password", lambda t: "x" * 12)
    monkeypatch.setattr(dlg, "_show_recovery", lambda code: shown.append(code))
    monkeypatch.setattr(pure_config, "save",
                        lambda: (_ for _ in ()).throw(OSError("disk")))
    from hranilka.ui.dialogs.settings import security
    monkeypatch.setattr(security, "themed_info",
                        lambda *a, **k: infos.append(a[2]))
    dlg._enc_enable()
    assert shown == ["REC-CODE"]
    assert "Предупреждение" in infos


# --- 4. seed-фраза -----------------------------------------------------------

def test_seed_gap_positions_round_trip(qapp):
    w = SeedPhraseWidget()
    w.set_text("abandon _ ability")
    words = w._words()
    assert words[:3] == ["abandon", "", "ability"]
    assert w.get_text() == "abandon _ ability"


def test_seed_legacy_value_loads_unchanged(qapp):
    w = SeedPhraseWidget()
    w.set_text("abandon ability able")
    assert w._words()[:3] == ["abandon", "ability", "able"]
    assert w.get_text() == "abandon ability able"


def test_seed_trailing_empty_cells_not_written(qapp):
    w = SeedPhraseWidget()
    w.set_text("abandon")
    assert w.get_text() == "abandon"


def test_seed_shrink_asks_confirmation(qapp, monkeypatch):
    w = SeedPhraseWidget()
    w.set_text(" ".join(["abandon"] * 15))
    assert w.count_combo.currentData() == 15
    monkeypatch.setattr(w, "_ask", lambda *a: False)
    w.count_combo.setCurrentIndex(0)
    assert w.count_combo.currentData() == 15
    assert len(w._words()) == 15
    monkeypatch.setattr(w, "_ask", lambda *a: True)
    w.count_combo.setCurrentIndex(0)
    assert len(w._words()) == 12


def test_seed_shrink_of_empty_cells_needs_no_confirmation(qapp, monkeypatch):
    w = SeedPhraseWidget()
    w.set_text("abandon")
    asked = []
    monkeypatch.setattr(w, "_ask", lambda *a: asked.append(1) or False)
    w.count_combo.setCurrentIndex(0)
    assert asked == []


def test_seed_paste_over_24_warns(qapp, monkeypatch):
    w = SeedPhraseWidget()
    warned = []
    monkeypatch.setattr(w, "_notify", lambda *a: warned.append(a))
    QApplication.clipboard().setText(" ".join(["abandon"] * 26))
    w.paste_phrase()
    assert len(warned) == 1
    assert len(w._words()) == 24


# --- 12. секретные вопросы ---------------------------------------------------

def test_secret_questions_loads_more_than_five_and_coerces_none(qapp, monkeypatch):
    from hranilka.ui.widgets import secret_questions as sq
    monkeypatch.setattr(sq, "_warn", lambda *a, **k: pytest.fail("modal"))
    w = SecretQuestionsWidget()
    w.set_data([{"q": f"q{i}", "a": None} for i in range(7)])
    data = w.get_data()
    assert len(data) == 7
    assert all(d["a"] == "" for d in data)
    # ручное добавление по-прежнему ограничено
    warned = []
    monkeypatch.setattr(sq, "_warn", lambda *a, **k: warned.append(1))
    w.add_row()
    assert warned and len(w.rows) == 7


# --- 8. срок карты -----------------------------------------------------------

@pytest.mark.parametrize("raw, shown", [
    ("2027-05", "05/27"), ("12/2027", "12/27"), ("5/27", "05/27"),
    ("2027-05-31", "05/27"), ("05/27", "05/27")])
def test_normalize_expiry(raw, shown):
    assert normalize_expiry(raw) == shown


@pytest.mark.parametrize("raw", ["13/27", "00/27", "abc", "", "1/2", None])
def test_normalize_expiry_rejects(raw):
    assert normalize_expiry(raw) is None


def test_expiry_field_legacy_and_unchanged_roundtrip(qapp):
    f = ExpiryField()
    f.set_text("2027-05")
    assert f.input.text() == "05/27"
    assert f.get_text() == "05/27"            # не тронуто — но в формате MM/YY
    from hranilka.core.fin_domain import parse_expiry
    assert parse_expiry(f.get_text()) is not None
    f.set_text("12/2027")
    assert f.input.text() == "12/27"
    f.set_text("garbage")
    assert f.get_text() == "garbage"          # не затирается


def test_expiry_field_edit_validates(qapp):
    f = ExpiryField()
    f.set_text("05/27")
    f.set_editable(True)
    f.input.textEdited.emit("")
    f.input.setText("13/27")
    assert f.get_text() == "05/27"            # неверный месяц — прежнее значение
    f.input.setText("11/28")
    assert f.get_text() == "11/28"
    f.input.setText("")
    assert f.get_text() == ""                 # осознанная очистка


# --- 9. частичная дата -------------------------------------------------------

def test_partial_date_keeps_previous_value(qapp):
    import datetime as dt
    f = CopyableDateField()
    f.set_date(dt.date(2025, 3, 4))
    f.set_editable(True)
    f.date_widget.setText("04.03.")
    assert f.get_date() == dt.datetime(2025, 3, 4)
    f.date_widget.setText("31.02.2025")
    assert f.get_date() == dt.datetime(2025, 3, 4)
    f.date_widget.setText("05.06.2026")
    assert f.get_date() == dt.datetime(2026, 6, 5)
    f.date_widget.setText("")
    assert f.get_date() is None


# --- 10. картинка в буфере идёт через защищённый канал -----------------------

def test_image_clipboard_uses_protected_mime(qapp, monkeypatch):
    seen = {}

    class Spy:
        def setMimeData(self, mime):
            seen["mime"] = mime

    monkeypatch.setattr(QApplication, "clipboard",
                        staticmethod(lambda: Spy()))
    msgs = []
    gallery_viewer.export_image_to_clipboard(_png(), lambda *a: msgs.append(a))
    assert isinstance(seen["mime"], QMimeData)
    assert seen["mime"].hasImage()
    import sys
    if sys.platform == "win32":
        from hranilka.ui import chrome
        fmt = chrome._WIN_MIME.format("CanIncludeInClipboardHistory")
        assert seen["mime"].data(fmt).data() == b"\x00\x00\x00\x00"


# --- 13. корзина -------------------------------------------------------------

class _BinDb:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def get_deleted_records(self):
        self.calls += 1
        return [{"type": "account", "id": 1, "name": "a", "deleted_at": ""}]

    def get_deleted_count(self):
        return 1

    def restore_account(self, rid):
        if self.fail:
            raise RuntimeError("boom")

    def empty_bin(self):
        if self.fail:
            raise RuntimeError("boom")


def test_recycle_bin_failure_not_marked_changed(qapp, pure_config, monkeypatch):
    infos = []
    monkeypatch.setattr(rb_mod, "themed_info", lambda *a, **k: infos.append(a[3]))
    db = _BinDb(fail=True)
    dlg = RecycleBinDialog(pure_config, db)
    before = db.calls
    dlg._restore()
    assert dlg.changed is False
    assert infos and "boom" in infos[0]
    assert db.calls == before + 1             # список обновлён
    monkeypatch.setattr(rb_mod, "themed_confirm", lambda *a, **k: True)
    dlg._empty()
    assert dlg.changed is False


def test_recycle_bin_success_marks_changed(qapp, pure_config):
    dlg = RecycleBinDialog(pure_config, _BinDb())
    dlg._restore()
    assert dlg.changed is True


# --- 5. экспорт: закрытие во время работы ------------------------------------

def test_export_dialog_ignores_close_while_busy(qapp, pure_config):
    dlg = ExportDialog(pure_config, object(), None, None, "t", show_fin=False)
    dlg.show()
    dlg._busy = True
    dlg.reject()
    dlg.close()
    assert dlg.isVisible()
    dlg._busy = False
    dlg.reject()
    assert not dlg.isVisible()


def test_export_dialog_force_close_while_busy(qapp, pure_config):
    dlg = ExportDialog(pure_config, object(), None, None, "t", show_fin=False)
    dlg.show()
    dlg._busy = True
    dlg.force_close()
    assert not dlg.isVisible()
    dlg._finish(error="late")                 # поздний результат безопасен
    assert not dlg.isVisible()


# --- 6. предпросмотр темы не утекает на диск ---------------------------------

def test_preview_not_persisted_by_config_save(qapp, tmp_path, monkeypatch):
    from hranilka.core import config as cfgmod
    path = tmp_path / "config.json"
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", path)
    cfg = cfgmod.Config()
    orig = cfg.get("text_color")
    dlg = SettingsDialog(cfg)
    cfg.set("text_color", "#123456")          # живой предпросмотр
    cfg.set("font_size", 31)
    dlg.config.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved.get("text_color") != "#123456"
    assert saved.get("font_size") != 31
    assert cfg.get("text_color") == "#123456"  # предпросмотр в памяти жив
    assert cfg.get("font_size") == 31
    assert orig != "#123456"


def test_delete_all_reverts_preview(qapp, pure_config, monkeypatch):
    from hranilka.ui.dialogs.settings import dialog as dmod
    monkeypatch.setattr(dmod, "themed_confirm", lambda *a, **k: True)
    orig = pure_config.get("text_color")
    dlg = SettingsDialog(pure_config)
    pure_config.set("text_color", "#123456")
    dlg._delete_all()
    assert pure_config.get("text_color") == orig
    assert dlg.delete_all_confirmed


def test_settings_force_close_reverts_silently(qapp, pure_config, monkeypatch):
    orig = pure_config.get("text_color")
    dlg = SettingsDialog(pure_config)
    dlg.show()
    pure_config.set("text_color", "#123456")
    monkeypatch.setattr(dlg, "_ask_close_action",
                        lambda: pytest.fail("prompt in force_close"))
    dlg.force_close()
    assert not dlg.isVisible()
    assert pure_config.get("text_color") == orig


# --- 7. сброс одного шортката с конфликтом -----------------------------------

def test_shortcut_reset_one_blocked_on_conflict(qapp, pure_config, monkeypatch):
    from hranilka.core import shortcuts
    from hranilka.ui.dialogs.settings import shortcuts_page
    infos = []
    monkeypatch.setattr(shortcuts_page, "themed_info",
                        lambda *a, **k: infos.append(a))
    dlg = SettingsDialog(pure_config)
    default = shortcuts.DEFAULTS["add_account"]
    dlg._sc_working["add_account"] = "Ctrl+Alt+F9"
    dlg._sc_working["add_service"] = default      # занято другим действием
    dlg._sc_reset_one("add_account")
    assert infos
    assert dlg._sc_working["add_account"] == "Ctrl+Alt+F9"
    dlg._sc_working["add_service"] = "Ctrl+Alt+F8"
    dlg._sc_reset_one("add_account")
    assert dlg._sc_working["add_account"] == default


# --- 14. после восстановления страница шифрования обновляется ----------------

def test_restore_refreshes_encryption_page(qapp, pure_config, monkeypatch):
    from hranilka.ui.dialogs.settings import dialog as dmod
    monkeypatch.setattr(dmod, "themed_confirm", lambda *a, **k: True)
    monkeypatch.setattr(dmod, "themed_info", lambda *a, **k: None)
    dlg = SettingsDialog(pure_config)

    class Db:
        encrypted = False

    db = Db()
    dlg.set_db(db)
    assert dlg._enc_off_group.isHidden() is False
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidgetItem
    item = QListWidgetItem("b.db")
    item.setData(Qt.UserRole, "b.db")
    dlg.backup_list.addItem(item)
    dlg.backup_list.setCurrentRow(0)
    dlg.set_restore_handler(lambda p: (db.__setattr__("encrypted", True), (True, None))[1])
    dlg._do_restore()
    assert dlg._enc_on_group.isHidden() is False
    assert dlg._enc_off_group.isHidden() is True


# --- 15. ревью-правки: связи, экспорт, даты, seed, предпросмотр --------------

import datetime as _dt
import sqlite3
from types import SimpleNamespace


class _LinkDb:
    """БД-заглушка: run_async отдаёт список, сессия управляется тестом."""

    def __init__(self):
        self.session = 1

    def current_session(self):
        return self.session

    def get_all_accounts(self):
        return []

    def export_subtree(self, *a, **k):
        return None

    async def run_async(self, fn, *a, **k):
        return [{"id": 1, "name": "A"}]


def _link_stub(cls, key_attr, tabs_attr, ctx_name, card_key):
    shown = []
    widget = SimpleNamespace(
        get_data=lambda: [], set_data=lambda d: shown.append(d),
        set_editable=lambda e: None)
    stub = SimpleNamespace(
        db=_LinkDb(), _card_gen=1, config=None, is_editing=True,
        **{key_attr: card_key, tabs_attr: SimpleNamespace(f_linked_accounts=widget)})
    setattr(stub, ctx_name, getattr(cls, ctx_name).__get__(stub))
    return stub, shown


@pytest.mark.parametrize("mutate", ["gen", "key", "session", "none"])
@pytest.mark.parametrize("kind", ["fin", "server"])
async def test_account_link_picker_guards_card(qapp, monkeypatch, kind, mutate):
    from hranilka.ui import fin_card, server_card
    if kind == "fin":
        mod, cls = fin_card, fin_card.FinCardMixin
        stub, shown = _link_stub(cls, "_current_fin", "fin_tabs",
                                 "_fin_link_ctx_valid", ("card", 5))
        run = cls._add_fin_account_link_async
        key_attr = "_current_fin"
    else:
        mod, cls = server_card, server_card.ServerCardMixin
        stub, shown = _link_stub(cls, "_current_server", "server_tabs",
                                 "_server_link_ctx_valid", ("server", 5))
        run = cls._add_server_account_link_async
        key_attr = "_current_server"
    stub._show_card_error = lambda *a: None

    def picker(config, parent, title, accounts, current):
        # смена карточки/сессии, пока диалог открыт
        if mutate == "gen":
            stub._card_gen += 1
        elif mutate == "key":
            setattr(stub, key_attr, ("x", 9))
        elif mutate == "session":
            stub.db.session += 1
        return [1], True

    monkeypatch.setattr(mod.theme, "themed_multiselect", picker)
    await run(stub)
    assert shown == ([[{"id": 1, "name": "A"}]] if mutate == "none" else [])


async def test_account_link_picker_stale_before_dialog(qapp, monkeypatch):
    from hranilka.ui import fin_card
    cls = fin_card.FinCardMixin
    stub, shown = _link_stub(cls, "_current_fin", "fin_tabs",
                             "_fin_link_ctx_valid", ("card", 5))
    opened = []

    class SwitchDb(_LinkDb):
        async def run_async(self, fn, *a, **k):
            stub._card_gen += 1                  # карточку сменили во время чтения
            return [{"id": 1, "name": "A"}]

    stub.db = SwitchDb()
    monkeypatch.setattr(fin_card.theme, "themed_multiselect",
                        lambda *a: opened.append(1) or ([1], True))
    await cls._add_fin_account_link_async(stub)
    assert opened == [] and shown == []


def test_reset_failed_selection_allows_retry(qapp):
    from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
    from hranilka.ui.account_card import AccountCardMixin
    tree = QTreeWidget()
    item = QTreeWidgetItem(tree, ["n"])
    tree.setCurrentItem(item)
    changes = []
    tree.currentItemChanged.connect(lambda c, p: changes.append(c))
    stub = SimpleNamespace(tree=tree, current_tree_item=item)
    AccountCardMixin._reset_failed_selection(stub)
    assert stub.current_tree_item is None
    assert tree.currentItem() is None
    assert changes == []                       # заглушку не перезатираем сигналом
    tree.setCurrentItem(item)                  # повторный клик → снова сигнал
    assert changes == [item]


async def test_leaf_load_failure_resets_selection(qapp):
    from hranilka.ui import fin_card
    from hranilka.ui.card_base import CardFlowMixin
    spec = fin_card._FIN_SPEC
    calls = []

    class Db:
        def current_session(self):
            return 1

        def __getattr__(self, name):
            return lambda *a, **k: None

        async def run_async(self, *a, **k):
            raise RuntimeError("boom")

    stub = SimpleNamespace(
        db=Db(), _card_gen=1, _edit_cache={}, is_editing=False,
        _current_fin=("card", 3),
        fin_tabs=SimpleNamespace(), current_tree_item=object(),
        _show_placeholder=lambda: calls.append("ph"),
        _show_card_error=lambda *a: calls.append("err"),
        _set_card_busy=lambda b: None,
        _reset_failed_selection=lambda: calls.append("reset"))
    await CardFlowMixin._leaf_load_async(stub, spec, ("card", 3), 1)
    assert stub._current_fin is None
    assert "reset" in calls and "err" in calls


# --- экспорт: should_abort / force_close ------------------------------------

def test_write_atomic_should_abort_wipes_tmp_and_skips_target(tmp_path):
    from hranilka.services import export
    target = tmp_path / "out.txt"

    def func(tree, opts, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("PLAINTEXT-SECRET")

    with pytest.raises(export.ExportAborted):
        export.write_atomic(func, [], None, str(target), should_abort=lambda: True)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []
    export.write_atomic(func, [], None, str(target), should_abort=lambda: False)
    assert target.read_text(encoding="utf-8") == "PLAINTEXT-SECRET"


async def test_export_force_close_during_format_leaves_no_file(
        qapp, pure_config, tmp_path):
    from hranilka.services import export
    db = _LinkDb()

    async def run_async(fn, *a, **k):
        return [{"type": "folder", "id": 1, "name": "f", "children": []}]

    db.run_async = run_async
    dlg = ExportDialog(pure_config, db, None, None, "t", show_fin=False)
    path = tmp_path / "exp.txt"

    def func(tree, opts, p):
        with open(p, "w", encoding="utf-8") as f:
            f.write("SECRET")
        dlg._forced_close = True               # force_close() во время записи (флаг из UI-потока)

    await dlg._run_export(func, str(path), export.Options())
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


async def test_export_session_change_during_format_leaves_no_file(
        qapp, pure_config, tmp_path):
    from hranilka.services import export
    db = _LinkDb()

    async def run_async(fn, *a, **k):
        return [{"type": "folder", "id": 1, "name": "f", "children": []}]

    db.run_async = run_async
    dlg = ExportDialog(pure_config, db, None, None, "t", show_fin=False)
    path = tmp_path / "exp.txt"

    def func(tree, opts, p):
        with open(p, "w", encoding="utf-8") as f:
            f.write("SECRET")
        db.session += 1

    await dlg._run_export(func, str(path), export.Options())
    assert list(tmp_path.iterdir()) == []


# --- экспорт: повреждённый payload -----------------------------------------

def test_bulk_marks_corrupt_payload_without_logging_data(caplog):
    from hranilka.data.database.bulk import DbBulkOpsMixin
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE fin_items (id, item_type, name, data)")
    con.execute("CREATE TABLE servers (id, name, data)")
    con.execute("INSERT INTO fin_items VALUES (1,'card','Good','{\"a\": 1}')")
    con.execute("INSERT INTO fin_items VALUES (2,'card','Bad','{SECRET-PIN')")
    con.execute("INSERT INTO servers VALUES (3,'Srv','not-json-TOPSECRET')")
    stub = SimpleNamespace(cursor=con.cursor(), _chunks=DbBulkOpsMixin._chunks)
    with caplog.at_level("WARNING"):
        fin = DbBulkOpsMixin._load_fin_items_bulk(stub, [1, 2], load_gallery=False)
        srv = DbBulkOpsMixin._load_servers_bulk(stub, [3], load_gallery=False)
    assert fin[1]["corrupt"] is False and fin[1]["payload"] == {"a": 1}
    assert fin[2]["corrupt"] is True and fin[2]["payload"] == {}
    assert srv[3]["corrupt"] is True
    assert "SECRET" not in caplog.text

    from hranilka.services import export
    tree = [{"type": "folder", "children": [
        {"type": "card", "name": "n2", "fin": fin[2]},
        {"type": "server", "name": "n3", "server": srv[3]},
        {"type": "card", "name": "n1", "fin": fin[1]}]}]
    assert export.corrupted_names(tree) == ["Bad", "Srv"]


def test_export_dialog_finish_warns_about_corrupt(qapp, pure_config, monkeypatch):
    from hranilka.ui.dialogs import export_dialog as ed
    texts = []
    monkeypatch.setattr(ed, "themed_info", lambda c, p, t, text: texts.append(text))
    dlg = ExportDialog(pure_config, object(), None, None, "t", show_fin=False)
    dlg._finish(path="x.txt", corrupted=["Bad"])
    assert "повреждены" in texts[-1] and "Bad" in texts[-1]
    dlg2 = ExportDialog(pure_config, object(), None, None, "t", show_fin=False)
    dlg2._finish(path="x.txt")
    assert "повреждены" not in texts[-1]


# --- даты --------------------------------------------------------------------

def test_date_field_partial_shows_hint_and_copies_displayed(qapp):
    f = CopyableDateField()
    f.set_date(_dt.date(2025, 3, 4))
    f.set_editable(True)
    assert f.warn_label.isHidden()
    f.date_widget.setText("04.03.")
    assert not f.warn_label.isHidden()
    assert "[!]" in f.warn_label.text()
    f.date_widget.setText("05.06.2026")
    assert f.warn_label.isHidden()
    f.set_editable(False)
    # показано 05.06.2026 (не загруженное 04.03.2025) — копируется показанное
    f.do_copy()
    assert QApplication.clipboard().text() == "05.06.2026"


def test_iso_date_field_partial_hint(qapp):
    from hranilka.ui.widgets.fields import CopyableIsoDateField
    f = CopyableIsoDateField()
    f.set_text("2025-03-04")
    f.set_editable(True)
    f._inner.date_widget.setText("04.03.")
    assert "[!]" in f._inner.warn_label.text()
    assert f.get_text() == "2025-03-04"


def test_expiry_legacy_unedited_normalized_and_parsable(qapp):
    from hranilka.core.fin_domain import parse_expiry
    f = ExpiryField()
    f.set_text("2027-12")
    assert f.get_text() == "12/27"
    assert parse_expiry(f.get_text()) is not None


def test_expiry_invalid_edit_hint_visible(qapp):
    f = ExpiryField()
    f.set_text("05/27")
    f.set_editable(True)
    f.input.textEdited.emit("")
    f.input.setText("13/27")
    assert not f.warn_label.isHidden()
    assert "[!]" in f.warn_label.text() and "прежнее" in f.warn_label.text()
    assert f.get_text() == "05/27"


# --- seed --------------------------------------------------------------------

def test_seed_copy_gives_real_words_and_warns_on_gaps(qapp, monkeypatch):
    w = SeedPhraseWidget()
    warned = []
    monkeypatch.setattr(w, "_notify", lambda *a: warned.append(a))
    w.set_text("abandon _ ability")
    w.do_copy()
    assert QApplication.clipboard().text() == "abandon ability"
    assert len(warned) == 1
    warned.clear()
    w.set_text("abandon ability able")
    w.do_copy()
    assert QApplication.clipboard().text() == "abandon ability able"
    assert warned == []


def test_seed_literal_underscore_rejected(qapp):
    w = SeedPhraseWidget()
    w.set_editable(True)
    edit = w._cells[0][0]
    QTest.keyClicks(edit, "_")
    assert edit.text() == ""
    QTest.keyClicks(edit, "a_b")
    assert edit.text() == "a_b"


def test_export_seed_has_no_gap_marks():
    from hranilka.core.fin_types import FieldSpec
    from hranilka.services import export
    field = FieldSpec("seed_phrase", "Seed", "seed", "Seed", secret=True)
    opts = export.Options(include_fin_secrets=True)
    out = export._fin_field_value(field, {"seed_phrase": "a _ c"}, opts)
    assert out == "a c"


# --- предпросмотр конфига ----------------------------------------------------

def test_config_preview_any_save_writes_committed(tmp_path, monkeypatch):
    from hranilka.core import config as cfgmod
    path = tmp_path / "config.json"
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", path)
    cfg = cfgmod.Config()
    orig = cfg.get("text_color")
    cfg.begin_preview({"text_color": orig})
    cfg.set("text_color", "#123456")
    cfg.set("sort_mode", "name")               # не предпросмотр — пишется
    cfg.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["text_color"] == orig and saved["sort_mode"] == "name"
    assert cfg.get("text_color") == "#123456"
    cfg.end_preview()
    cfg.end_preview()                          # идемпотентно
    cfg.save()
    assert json.loads(path.read_text(encoding="utf-8"))["text_color"] == "#123456"


def test_settings_dialog_preview_released_on_every_close(qapp, tmp_path, monkeypatch):
    from hranilka.core import config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    for how in ("force", "reject", "accept"):
        cfg = cfgmod.Config()
        dlg = SettingsDialog(cfg)
        assert cfg._preview_committed is not None
        dlg._ask_close_action = lambda: "discard"
        cfg.set("text_color", "#123456")
        {"force": dlg.force_close, "reject": dlg.reject,
         "accept": dlg.accept}[how]()
        assert cfg._preview_committed is None, how


def test_settings_apply_saves_new_values(qapp, tmp_path, monkeypatch):
    from hranilka.core import config as cfgmod
    path = tmp_path / "config.json"
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", path)
    cfg = cfgmod.Config()
    dlg = SettingsDialog(cfg)
    dlg.font_size_combo.setCurrentText("20")
    dlg._apply_settings()
    assert json.loads(path.read_text(encoding="utf-8"))["font_size"] == 20
    assert cfg._preview_committed is None


def test_failed_apply_keeps_dialog_and_preview_protection(qapp, tmp_path, monkeypatch):
    from hranilka.core import config as cfgmod
    from hranilka.ui.dialogs.settings import dialog as dlgmod
    path = tmp_path / "config.json"
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", path)
    cfg = cfgmod.Config()
    cfg.save()
    size = cfg.get("font_size")
    dlg = SettingsDialog(cfg)
    dlg.font_size_combo.setCurrentText("20")
    shown = []
    monkeypatch.setattr(dlgmod, "themed_info", lambda *a: shown.append(a))
    real_save = cfg.save

    def broken():
        raise OSError("disk full")

    monkeypatch.setattr(cfg, "save", broken)
    dlg._apply_settings()
    assert shown and dlg.result() == 0
    assert cfg._preview_committed is not None    # защита предпросмотра на месте
    monkeypatch.setattr(cfg, "save", real_save)
    cfg.save()
    assert json.loads(path.read_text(encoding="utf-8"))["font_size"] == size
    dlg.force_close()


def test_unlock_exit_clears_pasted_password(qapp, tmp_path, monkeypatch):
    from hranilka.ui.dialogs import unlock as u
    clip = QApplication.clipboard()
    clip.setText("secret")
    dlg = u.UnlockDialog.__new__(u.UnlockDialog)
    calls = []
    monkeypatch.setattr(u.UnlockDialog, "_clear_pasted_secret",
                        lambda self: calls.append(1))
    monkeypatch.setattr(u.ThemedDialog, "reject", lambda self: None)
    dlg._busy = False
    dlg._gen = 0
    u.UnlockDialog.reject(dlg)
    assert calls == [1]
