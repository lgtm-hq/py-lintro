"""Tests for TaploPlugin error handling, timeouts, and edge cases."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.taplo import TaploPlugin
from tests.test_samples_helpers import copy_sample

# Tests for timeout handling in check method


def test_check_with_timeout(
    taplo_plugin: TaploPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout correctly.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "config",
        "taplo",
        "taplo_clean.toml",
        dest_name="test.toml",
    )

    with patch.object(
        taplo_plugin,
        "_run_subprocess",
        side_effect=subprocess.TimeoutExpired(cmd=["taplo"], timeout=30),
    ):
        result = taplo_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(result.output).contains("timed out")


def test_check_with_timeout_on_format_check(
    taplo_plugin: TaploPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout during format check correctly.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "config",
        "taplo",
        "taplo_clean.toml",
        dest_name="test.toml",
    )

    with patch.object(
        taplo_plugin,
        "_run_subprocess",
        side_effect=[
            (True, ""),  # lint succeeds
            subprocess.TimeoutExpired(cmd=["taplo"], timeout=30),  # fmt times out
        ],
    ):
        result = taplo_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


# Tests for timeout handling in fix method


def test_fix_with_timeout(
    taplo_plugin: TaploPlugin,
    tmp_path: Path,
) -> None:
    """Fix handles timeout correctly.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "config",
        "taplo",
        "taplo_clean.toml",
        dest_name="test.toml",
    )

    with patch.object(
        taplo_plugin,
        "_run_subprocess",
        side_effect=subprocess.TimeoutExpired(cmd=["taplo"], timeout=30),
    ):
        result = taplo_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_fix_with_timeout_during_fix_command(
    taplo_plugin: TaploPlugin,
    tmp_path: Path,
) -> None:
    """Fix handles timeout during fix command correctly.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "config",
        "taplo",
        "taplo_unformatted.toml",
        dest_name="test.toml",
    )

    format_issue = """error[formatting]: the file is not properly formatted
  --> test.toml:2:1
   |
 2 | name="test"
   | ^ formatting issue
"""

    with patch.object(
        taplo_plugin,
        "_run_subprocess",
        side_effect=[
            (False, format_issue),  # initial format check
            (True, ""),  # initial lint check
            subprocess.TimeoutExpired(cmd=["taplo"], timeout=30),  # fix times out
        ],
    ):
        result = taplo_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")
    assert_that(result.initial_issues_count).is_equal_to(1)
