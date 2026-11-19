"""Mypy restores its exclude patterns when preparation raises (#2400)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.mypy.definition import MypyPlugin


def test_check_restores_excludes_when_prepare_raises(
    mypy_plugin: MypyPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The temporary discovery excludes never outlive a failed ``prepare``.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
        tmp_path: Directory used as the working directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "module.py").write_text("x = 1\n")
    mypy_plugin.exclude_patterns = ["custom/*"]

    with (
        patch.object(mypy_plugin, "prepare", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        mypy_plugin.check([str(tmp_path)], {})

    assert_that(mypy_plugin.exclude_patterns).is_equal_to(["custom/*"])
