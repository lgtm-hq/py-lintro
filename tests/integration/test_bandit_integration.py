"""Integration tests for Bandit tool (security linter)."""

import contextlib
import os
import tempfile

from assertpy import assert_that
from loguru import logger

from lintro.models.core.tool_result import ToolResult
from lintro.plugins import ToolRegistry
from tests.integration._tools import require_tool

pytestmark = require_tool("bandit")


def test_bandit_detects_issues_on_sample_file() -> None:
    """Run BanditTool against a known vulnerable sample and expect findings."""
    tool = ToolRegistry.get("bandit")
    assert_that(tool).is_not_none()
    # Clear exclude patterns to allow scanning test_samples
    tool.exclude_patterns = []
    sample = os.path.abspath("test_samples/tools/python/bandit/bandit_violations.py")
    assert_that(os.path.exists(sample)).is_true()
    result: ToolResult = tool.check([sample], {})
    assert_that(isinstance(result, ToolResult)).is_true()
    assert_that(result.name).is_equal_to("bandit")
    assert_that(result.issues_count > 0).is_true()
    logger.info(f"[TEST] bandit found {result.issues_count} issues on sample file")


def test_bandit_no_crash_on_clean_temp_file() -> None:
    """Bandit should handle a trivial (clean) temp file gracefully."""
    tool = ToolRegistry.get("bandit")
    assert_that(tool).is_not_none()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def ok():\n    return 0\n")
        f.flush()
        path = f.name
    try:
        result: ToolResult = tool.check([path], {})
        assert_that(isinstance(result, ToolResult)).is_true()
        assert_that(result.name).is_equal_to("bandit")
        assert_that(result.issues_count >= 0).is_true()
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
