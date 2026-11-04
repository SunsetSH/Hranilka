"""Аудит S37/B15: для секретов — только secrets, не random."""
import ast
from pathlib import Path

GEN = Path(__file__).resolve().parent.parent / "hranilka" / "generators"


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            names.add(n.module or "")
    return names


def test_password_gen_uses_secrets_not_random():
    imps = _imports(GEN / "password_gen.py")
    assert "secrets" in imps
    assert "random" not in imps
    assert "hranilka.generators.pd_generator" not in imps


def test_wordlist_data_has_no_imports():
    assert _imports(GEN / "wordlist_data.py") == set()


def test_util_has_no_password_generator():
    from hranilka.core import util
    assert not hasattr(util, "generate_password")
