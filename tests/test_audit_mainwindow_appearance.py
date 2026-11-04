"""P5: debounce/skip в apply_appearance, кэш стрелок, структура миксинов."""
import pytest

from hranilka.ui import appearance, db_lifecycle, sidebar


@pytest.fixture
def window(qapp, tmp_path, monkeypatch, dispose_window):
    from hranilka.core import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    from hranilka.ui import main_window as main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    win = main.MainWindow()
    yield win
    dispose_window(win)


def test_mixins_in_mro(window):
    cls = type(window)
    for m in (appearance.AppearanceMixin, sidebar.SidebarMixin, db_lifecycle.DbLifecycleMixin):
        assert issubclass(cls, m)


def test_same_appearance_not_repolished(window):
    calls = []
    orig = window.setStyleSheet
    window.setStyleSheet = lambda s: (calls.append(s), orig(s))[1]
    window.apply_appearance()
    assert calls == []


def test_only_changed_part_restyled(window):
    window.config.set("text_color", "#00FF00")
    window.apply_appearance()
    first_main = window.styleSheet()
    tree_calls = []
    orig = window.tree.setStyleSheet
    window.tree.setStyleSheet = lambda s: (tree_calls.append(s), orig(s))[1]
    window.config.set("text_color", "#00FF00")
    window._appearance_sig = None            # принудительно пересчитать QSS
    window.apply_appearance()
    assert tree_calls == [] and window.styleSheet() == first_main


def test_arrow_pngs_not_rewritten_when_colors_unchanged(window):
    renders = []
    orig = window._render_branch_arrow_files
    window._render_branch_arrow_files = lambda *a: (renders.append(a), orig(*a))[1]
    window.config.set("main_bg_color", "#123456")      # стрелки зависят от text/tree_bg
    window.apply_appearance()
    assert renders == []
    window.config.set("text_color", "#ABCDEF")
    window.apply_appearance()
    assert len(renders) == 1


def test_request_appearance_debounced(window, qapp):
    n = []
    window.apply_appearance = lambda: n.append(1)
    window._appearance_timer = None
    for _ in range(5):
        window.request_appearance()
    assert n == []
    window.flush_appearance()
    assert n == [1]
    window.flush_appearance()                # повторный flush ничего не делает
    assert n == [1]


def test_tree_hover_visible_with_equal_backgrounds(window):
    window.config.set("tree_bg_color", "#0000AA")
    window.config.set("main_bg_color", "#0000AA")
    window._appearance_sig = None
    window.apply_appearance()
    qss = window.tree.styleSheet()
    hover_line = [l for l in qss.splitlines() if "item:hover" in l][0]
    assert "#0000aa" not in hover_line.lower()
