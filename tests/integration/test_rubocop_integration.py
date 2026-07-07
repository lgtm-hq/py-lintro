"""Integration tests for the RuboCop tool (Ruby linter/formatter).

These tests require the ``rubocop`` binary on PATH and are skipped otherwise.
They exercise the real binary against Ruby fixtures for both the check and fix
paths, asserting the fix invariant (initial == fixed + remaining).
"""

from __future__ import annotations

import os
import shutil

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins import ToolRegistry

REQUIRES_RUBOCOP = pytest.mark.skipif(
    shutil.which("rubocop") is None,
    reason="rubocop not installed on PATH; skip integration test.",
)

VIOLATIONS = "test_samples/tools/ruby/rubocop/rubocop_violations.rb"
CLEAN = "test_samples/tools/ruby/rubocop/rubocop_clean.rb"


@REQUIRES_RUBOCOP
def test_rubocop_detects_issues_on_sample() -> None:
    """RuboCop reports offenses on the violations fixture."""
    tool = ToolRegistry.get("rubocop")
    assert_that(tool).is_not_none()
    tool.exclude_patterns = []
    sample = os.path.abspath(VIOLATIONS)
    assert_that(os.path.exists(sample)).is_true()

    result: ToolResult = tool.check([sample], {})
    assert_that(result.name).is_equal_to("rubocop")
    assert_that(result.issues_count > 0).is_true()
    codes = [i.code for i in (result.issues or [])]
    assert_that(any("/" in c for c in codes)).is_true()


@REQUIRES_RUBOCOP
def test_rubocop_clean_sample_has_no_issues() -> None:
    """RuboCop reports no offenses on the clean fixture."""
    tool = ToolRegistry.get("rubocop")
    tool.exclude_patterns = []
    sample = os.path.abspath(CLEAN)
    assert_that(os.path.exists(sample)).is_true()

    result: ToolResult = tool.check([sample], {})
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.success).is_true()


@REQUIRES_RUBOCOP
def test_rubocop_fix_invariant(tmp_path: object) -> None:
    """Autocorrect reduces offenses and preserves the fix invariant.

    Args:
        tmp_path: Temporary directory to hold a writable copy of the fixture.
    """
    tool = ToolRegistry.get("rubocop")
    tool.exclude_patterns = []

    src = os.path.abspath(VIOLATIONS)
    dst = os.path.join(str(tmp_path), "v.rb")
    shutil.copyfile(src, dst)

    result: ToolResult = tool.fix([dst], {})
    assert_that(result.initial_issues_count).is_greater_than(0)
    assert_that(result.fixed_issues_count).is_greater_than(0)
    assert_that(result.initial_issues_count).is_equal_to(
        result.fixed_issues_count + result.remaining_issues_count,
    )
