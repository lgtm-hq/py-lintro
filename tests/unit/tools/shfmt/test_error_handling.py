"""Unit tests for shfmt plugin error handling."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from assertpy import assert_that

from lintro.parsers.shfmt.shfmt_parser import parse_shfmt_output
from lintro.tools.definitions.shfmt import ShfmtPlugin

if TYPE_CHECKING:
    pass


# =============================================================================
# Tests for timeout handling
# =============================================================================


def test_check_with_timeout(
    shfmt_plugin: ShfmtPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout correctly.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_script.sh"
    test_file.write_text('#!/bin/bash\necho "hello"\n')

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            shfmt_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=["shfmt"], timeout=30),
        ),
    ):
        result = shfmt_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()


def test_fix_with_timeout(
    shfmt_plugin: ShfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix handles timeout correctly.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_script.sh"
    test_file.write_text('#!/bin/bash\necho "hello"\n')

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            shfmt_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=["shfmt"], timeout=30),
        ),
    ):
        result = shfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_false()


# =============================================================================
# Tests for output parsing
# =============================================================================


def test_parse_shfmt_output_single_file() -> None:
    """Parse single file diff from shfmt output."""
    output = """--- script.sh
+++ script.sh
@@ -1,3 +1,3 @@
-if [  "$foo" = "bar" ]; then
+if [ "$foo" = "bar" ]; then
  echo "match"
 fi"""
    issues = parse_shfmt_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("script.sh")
    assert_that(issues[0].line).is_equal_to(1)
    assert_that(issues[0].message).contains("Needs formatting")
    assert_that(issues[0].fixable).is_true()


def test_parse_shfmt_output_multiple_files() -> None:
    """Parse multiple files diff from shfmt output."""
    output = """--- script1.sh
+++ script1.sh
@@ -1,2 +1,2 @@
-echo  "hello"
+echo "hello"
--- script2.sh
+++ script2.sh
@@ -1,2 +1,2 @@
-if [  1 ]; then
+if [ 1 ]; then"""
    issues = parse_shfmt_output(output)

    assert_that(issues).is_length(2)
    assert_that(issues[0].file).is_equal_to("script1.sh")
    assert_that(issues[1].file).is_equal_to("script2.sh")


def test_parse_shfmt_output_empty() -> None:
    """Parse empty output returns empty list."""
    issues = parse_shfmt_output("")

    assert_that(issues).is_empty()


def test_parse_shfmt_output_none() -> None:
    """Parse None output returns empty list."""
    issues = parse_shfmt_output(None)

    assert_that(issues).is_empty()


def test_parse_shfmt_output_with_orig_suffix() -> None:
    """Parse diff with .orig suffix in header."""
    output = """--- script.sh.orig
+++ script.sh
@@ -1,2 +1,2 @@
-echo  "hello"
+echo "hello" """
    issues = parse_shfmt_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("script.sh")
