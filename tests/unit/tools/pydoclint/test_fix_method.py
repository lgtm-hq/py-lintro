"""Tests for pydoclint plugin fix method."""

from __future__ import annotations

from pathlib import Path

import pytest

from lintro.tools.pydoclint.definition import PydoclintPlugin


def test_fix_raises_not_implemented(
    pydoclint_plugin: PydoclintPlugin,
    tmp_path: Path,
) -> None:
    """Fix method raises NotImplementedError.

    Args:
        pydoclint_plugin: The PydoclintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_module.py"
    test_file.write_text('"""Test module."""\n')

    with pytest.raises(NotImplementedError):
        pydoclint_plugin.fix([str(test_file)], {})
