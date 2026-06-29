"""Эмпирический поиск «лишнего» top-level окна (Баг: пустое окно со
стандартными рамками при переключении аккаунтов / add_item в галерее).

Идея: даже в offscreen режиме виджет без родителя при показе попадает в
QApplication.topLevelWidgets(). Ставим глобальный eventFilter, ловим QEvent.Show
на виджетах без родителя и печатаем стек создания — так находим ИСТОЧНИК.
"""
import os
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PySide6.QtCore import QObject, QEvent, QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

import widgets
from widgets import GalleryWidget


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _silence_dialogs(monkeypatch):
    monkeypatch.setattr(widgets, "_warn", lambda *a, **k: None)


def _png_bytes(w=20, h=20):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(0xFF8800)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


class _TopLevelSpy(QObject):
    """Ловит показ любого виджета, у которого нет родителя (=> top-level)."""

    def __init__(self):
        super().__init__()
        self.hits = []  # (typename, objectName, flags)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Show and isinstance(obj, QWidget):
            if obj.parent() is None and obj.isWindow():
                self.hits.append((
                    type(obj).__name__,
                    obj.objectName(),
                    str(obj.windowFlags()),
                ))
                txt = obj.text() if hasattr(obj, "text") else ""
                print("\n=== TOP-LEVEL SHOW ===")
                print("type      :", type(obj).__name__)
                print("text      :", txt)
                print("objectName:", obj.objectName())
                print("flags     :", obj.windowFlags())
                print("stack     :")
                print("".join(traceback.format_stack()[-12:]))
        return False


def _new_toplevels(before):
    after = set(QApplication.topLevelWidgets())
    return after - before


def test_gallery_add_item_no_floating_window(qapp):
    spy = _TopLevelSpy()
    qapp.installEventFilter(spy)
    try:
        before = set(QApplication.topLevelWidgets())

        gw = GalleryWidget()          # без родителя сам по себе — это ожидаемо
        gw.set_editable(True)         # режим, в котором видны кнопки [X] (триггер бага)
        before.add(gw)

        data = _png_bytes()
        gw.add_item(data, "a")
        gw.add_item(data, "b")
        gw.add_item(b"corrupt bytes", "c")
        qapp.processEvents()

        # Симуляция переключения аккаунта: очистка + повторное заполнение.
        gw.set_data([{"data": data, "desc": "x"}, {"data": data, "desc": "y"}])
        gw.set_data([])
        gw.set_data([{"data": data, "desc": "z"}])
        qapp.processEvents()

        new = _new_toplevels(before)
        new = {w for w in new if w is not gw}
    finally:
        qapp.removeEventFilter(spy)

    for w in new:
        print("UNEXPECTED:", type(w).__name__, w.objectName(),
              "parent=", w.parent(), "visible=", w.isVisible(),
              "flags=", w.windowFlags())

    assert not spy.hits, f"Показаны лишние top-level окна: {spy.hits}"
    assert not new, f"Появились лишние top-level виджеты: {new}"


def test_accounttabs_build_no_floating_window(qapp, tmp_path, monkeypatch):
    """Строим AccountTabs (WrappingTabWidget + FlowLayout) как в живом окне и
    наполняем галерею/коды/вопросы/связи — ловим лишние top-level окна."""
    import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    cfg = config.Config()
    from tabs import AccountTabs

    # Хост-окно, как в живом приложении: главное окно показано, а вкладки
    # строятся/наполняются внутри уже реализованной иерархии.
    host = QWidget()
    host_lay = QVBoxLayout(host)
    host.show()
    qapp.processEvents()

    spy = _TopLevelSpy()
    qapp.installEventFilter(spy)
    try:
        before = set(QApplication.topLevelWidgets())
        tabs = AccountTabs(config=cfg, parent=host)
        host_lay.addWidget(tabs)
        qapp.processEvents()
        before.add(tabs)
        tabs.set_all_editable(True)

        data = _png_bytes()
        # Наполняем все динамические виджеты (редактируемый режим: кнопки [X] видимы).
        tabs.f_gallery_widget.set_data([{"data": data, "desc": "a"},
                                        {"data": data, "desc": "b"}])
        tabs.f_codes_widget.set_data(["c1", "c2"])
        tabs.f_questions_widget.set_data([{"q": "q1", "a": "a1"}])
        tabs.f_linked.set_data([{"id": 1, "name": "x"}, {"id": 2, "name": "y"}])
        qapp.processEvents()

        # Переключаемся по вкладкам (FlowLayout / QStackedWidget).
        for i in range(tabs.count()):
            tabs.setCurrentIndex(i)
            qapp.processEvents()

        # Повторное наполнение (симуляция смены аккаунта).
        tabs.f_gallery_widget.set_data([{"data": data, "desc": "z"}])
        tabs.f_linked.set_data([])
        qapp.processEvents()

        new = _new_toplevels(before)
        new = {w for w in new if w is not tabs and w is not host}
    finally:
        qapp.removeEventFilter(spy)
        host.close()

    for w in new:
        print("UNEXPECTED:", type(w).__name__, w.objectName(),
              "parent=", w.parent(), "visible=", w.isVisible(),
              "flags=", w.windowFlags())

    assert not spy.hits, f"Показаны лишние top-level окна: {spy.hits}"
    assert not new, f"Появились лишние top-level виджеты: {new}"


def test_mainwindow_switch_accounts_no_floating_window(qapp, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    import main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    # Глушим возможные модальные предупреждения (например, срок пароля).
    import theme
    monkeypatch.setattr(theme, "themed_info", lambda *a, **k: None)

    win = main.MainWindow()
    spy = _TopLevelSpy()
    qapp.installEventFilter(spy)
    try:
        db = win.db
        fid = db.add_folder("Папка")
        sid = db.add_service("Сервис", fid)
        a1 = db.add_account(sid, "Acc1")
        a2 = db.add_account(sid, "Acc2")

        data = _png_bytes()
        # Кладём по картинке в галерею каждого аккаунта через storage.
        for aid in (a1, a2):
            st = db.load_account(aid)
            st["gallery"] = [{"data": data, "desc": "g"}]
            db.save_account(aid, st)

        win._reload_tree()
        before = set(QApplication.topLevelWidgets())

        # Переключаемся между аккаунтами несколько раз.
        for _ in range(3):
            win._select_node("account", a1)
            qapp.processEvents()
            win._select_node("account", a2)
            qapp.processEvents()

        new = _new_toplevels(before)
    finally:
        qapp.removeEventFilter(spy)
        try:
            win.vault.shutdown()
        except Exception:
            pass
        try:
            win._instance_lock.release()
        except Exception:
            pass

    for w in new:
        print("UNEXPECTED:", type(w).__name__, w.objectName(),
              "parent=", w.parent(), "visible=", w.isVisible(),
              "flags=", w.windowFlags())

    assert not spy.hits, f"Показаны лишние top-level окна: {spy.hits}"
    assert not new, f"Появились лишние top-level виджеты: {new}"
