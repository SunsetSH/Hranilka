"""Enter в окне пароля: регрессия закрытия окна как по кнопке «Выход»."""
import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from hranilka.crypto import store as cs
from hranilka.ui.dialogs.unlock import UnlockDialog

RESULT = (b"db", b"key", {})


@pytest.fixture
def dialog(qapp, pure_config, monkeypatch):
    release = threading.Event()

    def slow_unlock(_container, _secret, is_recovery=False):
        release.wait(5)
        return RESULT

    monkeypatch.setattr(cs, "unlock", slow_unlock)
    dlg = UnlockDialog(pure_config, b"container")
    dlg.release = release
    dlg.show()
    dlg.activateWindow()
    dlg._field.setFocus()
    dlg._field.setText("secret")
    qapp.processEvents()
    yield dlg
    release.set()
    dlg.close()


def test_enter_unlocks_instead_of_closing(qapp, dialog):
    QTest.keyClick(dialog._field, Qt.Key.Key_Return)
    qapp.processEvents()
    assert dialog.isVisible() and dialog._busy
    assert dialog.focusWidget() is dialog._field

    dialog.release.set()
    for _ in range(300):
        if dialog.result_data is not None:
            break
        QTest.qWait(10)
    assert dialog.result_data == RESULT
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_repeated_enter_while_decrypting_keeps_dialog_open(qapp, dialog):
    for _ in range(3):
        QTest.keyClick(dialog._field, Qt.Key.Key_Return)
        qapp.processEvents()
    assert dialog.isVisible() and dialog._busy
