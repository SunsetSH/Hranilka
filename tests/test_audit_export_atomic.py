"""Аудит S6: временный файл экспорта не остаётся после сбоя/успеха."""
import os

import pytest

from hranilka.services import export


def _writer(text):
    def fn(tree, opts, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return fn


def test_success_publishes_and_no_tmp(tmp_path):
    target = tmp_path / "out.txt"
    export.write_atomic(_writer("data"), [], None, str(target))
    assert target.read_text(encoding="utf-8") == "data"
    assert os.listdir(tmp_path) == ["out.txt"]


def test_formatter_failure_leaves_no_tmp_and_target(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("old", encoding="utf-8")

    def fn(tree, opts, path):
        _writer("partial")(tree, opts, path)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        export.write_atomic(fn, [], None, str(target))
    assert target.read_text(encoding="utf-8") == "old"
    assert os.listdir(tmp_path) == ["out.txt"]


def test_replace_failure_leaves_no_tmp(tmp_path, monkeypatch):
    target = tmp_path / "out.txt"

    def bad_replace(src, dst):
        raise PermissionError("locked")

    monkeypatch.setattr(export.os, "replace", bad_replace)
    with pytest.raises(PermissionError):
        export.write_atomic(_writer("x"), [], None, str(target))
    assert os.listdir(tmp_path) == []
