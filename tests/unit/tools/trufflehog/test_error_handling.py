"""Unit tests for trufflehog plugin error handling."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.trufflehog import TrufflehogPlugin


def test_check_timeout(trufflehog_plugin: TrufflehogPlugin, tmp_path: Path) -> None:
    """A subprocess timeout should be reported as a failure.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text('"""Module."""\n')

    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        side_effect=subprocess.TimeoutExpired(cmd=["trufflehog"], timeout=60),
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_check_execution_failure(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """An OSError while running should be reported as a failure.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text('"""Module."""\n')

    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        side_effect=OSError("failed to execute trufflehog"),
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("TruffleHog failed")


def test_fix_raises_not_implemented(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """The fix method should raise NotImplementedError.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text("TOKEN = 'ghp_fake'\n")

    with pytest.raises(NotImplementedError, match="cannot automatically fix"):
        trufflehog_plugin.fix([str(test_file)], {})
