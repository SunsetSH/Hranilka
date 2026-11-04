"""Чистая логика общего потока карточек (hranilka/ui/card_base.py)."""

from hranilka.ui.card_base import (
    CardFlowMixin, append_orphan_to_draft, edit_flag_stale,
    orphan_gallery_entry)


def test_orphan_gallery_entry_format():
    assert orphan_gallery_entry("d", b"x") == {
        "desc": "d", "data": b"x", "image_id": None, "blob_size": None}


def test_append_orphan_creates_gallery_key():
    draft = {"storage": {}, "links": []}
    append_orphan_to_draft(draft, "a", b"1")
    append_orphan_to_draft(draft, "b", b"2")
    assert [g["desc"] for g in draft["storage"]["gallery"]] == ["a", "b"]


def test_append_orphan_keeps_existing_gallery():
    draft = {"storage": {"gallery": [{"desc": "old"}]}}
    append_orphan_to_draft(draft, "new", b"")
    assert len(draft["storage"]["gallery"]) == 2


def test_edit_flag_stale():
    assert not edit_flag_stale(None, 5)
    assert not edit_flag_stale(5, 5)
    assert edit_flag_stale(5, 6)
    assert edit_flag_stale(("fin", 1), None)


class _Btn:
    def __init__(self):
        self.visible = None

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


class _Card(CardFlowMixin):
    def __init__(self):
        self.edit_btn, self.save_btn, self.cancel_btn = _Btn(), _Btn(), _Btn()


def test_set_edit_mode_ui_toggles_buttons():
    c = _Card()
    c._set_edit_mode_ui(True)
    assert (c.edit_btn.visible, c.save_btn.visible, c.cancel_btn.visible) == (
        False, True, True)
    c._set_edit_mode_ui(False)
    assert (c.edit_btn.visible, c.save_btn.visible, c.cancel_btn.visible) == (
        True, False, False)


def test_drop_stale_edit_flag():
    c = _Card()
    c.flag = ("fin", 1)
    c._drop_stale_edit_flag("flag", ("fin", 1))
    assert c.flag == ("fin", 1)
    c._drop_stale_edit_flag("flag", ("fin", 2))
    assert c.flag is None
