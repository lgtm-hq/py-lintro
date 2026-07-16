"""Unit tests for gitleaks plugin error handling and edge cases."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.gitleaks import GitleaksPlugin


def test_check_with_timeout(
    gitleaks_plugin: GitleaksPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout correctly.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_module.py"
    test_file.write_text('"""Test module."""\n')

    with patch.object(
        gitleaks_plugin,
        "_run_subprocess",
        side_effect=subprocess.TimeoutExpired(cmd=["gitleaks"], timeout=60),
    ):
        result = gitleaks_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_check_with_execution_failure(
    gitleaks_plugin: GitleaksPlugin,
    tmp_path: Path,
) -> None:
    """Check handles execution failure correctly.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_module.py"
    test_file.write_text('"""Test module."""\n')

    with patch.object(
        gitleaks_plugin,
        "_run_subprocess",
        side_effect=OSError("Failed to execute gitleaks"),
    ):
        result = gitleaks_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("Gitleaks failed")


def test_fix_raises_not_implemented_error(
    gitleaks_plugin: GitleaksPlugin,
    tmp_path: Path,
) -> None:
    """Fix method raises NotImplementedError.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_module.py"
    test_file.write_text('API_KEY = "secret123"\n')

    with pytest.raises(NotImplementedError, match="cannot automatically fix"):
        gitleaks_plugin.fix([str(test_file)], {})
