"""M-02: маскировка секретных полей карточки (ответы на секретные вопросы,
резервные коды 2FA, recovery-фраза) одной кнопкой на вкладку, плюс 5а —
пустые строки исчезают из интерфейса сразу после выхода из режима правки.

Виджеты строятся offscreen (qapp из conftest), без MainWindow.
"""
import pytest

from PySide6.QtWidgets import QApplication, QLineEdit

from hranilka.ui.widgets import (CodeListWidget, MaskedTextEdit,
                                 SecretQuestionsWidget)


# ─── Секретные вопросы: маскируются только ответы ────────────────────────────

def test_questions_answers_masked_in_view(qapp, pure_config):
    w = SecretQuestionsWidget(config=pure_config)
    w.set_data([{"q": "Кличка кота", "a": "Барсик"},
                {"q": "Город детства", "a": "Гомель"}])
    for q_edit, a_edit, _btn, _w in w.rows:
        assert q_edit.echoMode() == QLineEdit.Normal, "вопрос виден всегда"
        assert a_edit.echoMode() == QLineEdit.Password, "ответ скрыт в просмотре"


def test_questions_single_button_reveals_all(qapp, pure_config):
    w = SecretQuestionsWidget(config=pure_config)
    w.set_data([{"q": "В1", "a": "О1"}, {"q": "В2", "a": "О2"}])
    w.reveal_btn.setChecked(True)
    assert all(a.echoMode() == QLineEdit.Normal for _q, a, _b, _w in w.rows)
    w.reveal_btn.setChecked(False)
    assert all(a.echoMode() == QLineEdit.Password for _q, a, _b, _w in w.rows)


def test_questions_edit_mode_shows_and_view_remasks(qapp, pure_config):
    w = SecretQuestionsWidget(config=pure_config)
    w.set_data([{"q": "В", "a": "О"}])
    w.set_editable(True)
    assert w.rows[0][1].echoMode() == QLineEdit.Normal
    w.set_editable(False)
    assert w.rows[0][1].echoMode() == QLineEdit.Password
    assert not w.reveal_btn.isChecked()


def test_questions_empty_rows_pruned_after_save(qapp, pure_config):
    """5а: пустая строка (оба поля) убирается из UI при выходе из правки;
    частично заполненная — остаётся (она и сохраняется в БД)."""
    w = SecretQuestionsWidget(config=pure_config)
    w.set_editable(True)
    w.add_row("Вопрос", "Ответ")
    w.add_row("", "")               # пустая — не попадёт в БД
    w.add_row("Только вопрос", "")  # частичная — сохраняется
    w.set_editable(False)
    assert len(w.rows) == 2
    assert w.get_data() == [{"q": "Вопрос", "a": "Ответ"},
                            {"q": "Только вопрос", "a": ""}]


# ─── Резервные коды 2FA ───────────────────────────────────────────────────────

def test_codes_masked_in_view_and_toggle(qapp, pure_config):
    w = CodeListWidget(config=pure_config)
    w.set_data(["AAAA-1111", "BBBB-2222"])
    assert all(e.echoMode() == QLineEdit.Password for e, _b, _w in w.rows)
    w.reveal_btn.setChecked(True)
    assert all(e.echoMode() == QLineEdit.Normal for e, _b, _w in w.rows)
    w.reveal_btn.setChecked(False)
    assert all(e.echoMode() == QLineEdit.Password for e, _b, _w in w.rows)


def test_codes_copy_works_while_masked(qapp, pure_config):
    """Копирование остаётся явной кнопкой и копирует настоящий код."""
    w = CodeListWidget(config=pure_config)
    w.set_data(["SECRET-CODE-42"])
    w.copy_code(w.rows[0][0].text())
    assert QApplication.clipboard().text() == "SECRET-CODE-42"


def test_codes_edit_mode_shows_then_remasks_and_prunes(qapp, pure_config):
    w = CodeListWidget(config=pure_config)
    w.set_data(["CODE-1"])
    w.set_editable(True)
    assert w.rows[0][0].echoMode() == QLineEdit.Normal
    w.add_code("")                   # пустой код — не попадёт в БД
    w.set_editable(False)
    assert len(w.rows) == 1          # 5а: пустая строка убрана из UI
    assert w.rows[0][0].echoMode() == QLineEdit.Password
    assert w.get_data() == ["CODE-1"]


# ─── Recovery-фраза (MaskedTextEdit) ─────────────────────────────────────────

_PHRASE = "correct horse battery staple"


def test_masked_textedit_hides_in_view(qapp):
    w = MaskedTextEdit()
    w.set_text(_PHRASE)
    shown = w.text_edit.toPlainText()
    assert _PHRASE not in shown and set(shown) == {"•"}
    assert len(shown) == len(_PHRASE)
    assert w.get_text() == _PHRASE   # настоящее значение не теряется


def test_masked_textedit_reveal_toggle(qapp):
    w = MaskedTextEdit()
    w.set_text(_PHRASE)
    w.reveal_btn.setChecked(True)
    w._toggle_reveal()               # клик: isChecked уже True
    assert w.text_edit.toPlainText() == _PHRASE
    w.reveal_btn.setChecked(False)
    w._toggle_reveal()
    assert _PHRASE not in w.text_edit.toPlainText()


def test_masked_textedit_edit_roundtrip(qapp):
    w = MaskedTextEdit()
    w.set_text(_PHRASE)
    w.set_editable(True)
    assert w.text_edit.toPlainText() == _PHRASE   # правка — по настоящему тексту
    w.text_edit.setPlainText("new phrase")
    assert w.get_text() == "new phrase"
    w.set_editable(False)
    assert w.get_text() == "new phrase"           # значение зафиксировано
    assert "new phrase" not in w.text_edit.toPlainText()  # и снова скрыто


def test_masked_textedit_copies_secret_not_mask(qapp):
    w = MaskedTextEdit()
    w.set_text(_PHRASE)
    w.do_copy()
    assert QApplication.clipboard().text() == _PHRASE
