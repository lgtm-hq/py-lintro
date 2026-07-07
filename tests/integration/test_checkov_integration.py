"""Integration tests for Checkov (Infrastructure-as-Code security scanner)."""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.checkov.checkov_issue import CheckovIssue
from lintro.plugins import ToolRegistry


@pytest.mark.skipif(
    shutil.which("checkov") is None,
    reason="Checkov not installed on PATH; skip integration test.",
)
def test_checkov_detects_issues_on_sample_file() -> None:
    """Run Checkov against a seeded Terraform sample and expect findings."""
    tool = ToolRegistry.get("checkov")
    assert_that(tool).is_not_none()
    tool.exclude_patterns = []
    sample = os.path.abspath(
        "test_samples/tools/terraform/checkov/checkov_violations.tf",
    )
    assert_that(os.path.exists(sample)).is_true()

    result: ToolResult = tool.check([sample], {})
    assert_that(isinstance(result, ToolResult)).is_true()
    assert_that(result.name).is_equal_to("checkov")
    assert_that(result.success).is_false()
    assert_that(result.issues_count > 0).is_true()

    issue = result.issues[0]
    assert_that(issue).is_instance_of(CheckovIssue)
    assert_that(issue.check_id).starts_with("CKV")
    assert_that(issue.resource).is_not_empty()
    logger.info(f"[TEST] checkov found {result.issues_count} issues on sample file")


@pytest.mark.skipif(
    shutil.which("checkov") is None,
    reason="Checkov not installed on PATH; skip integration test.",
)
def test_checkov_clean_on_trivial_terraform() -> None:
    """Checkov handles a resource-free Terraform file without findings."""
    tool = ToolRegistry.get("checkov")
    tool.exclude_patterns = []
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tf", delete=False) as f:
        f.write('output "noop" {\n  value = "ok"\n}\n')
        f.flush()
        path = f.name
    try:
        result: ToolResult = tool.check([path], {})
        assert_that(result.name).is_equal_to("checkov")
        assert_that(result.issues_count).is_equal_to(0)
        assert_that(result.success).is_true()
    finally:
        if os.path.exists(path):
            os.unlink(path)
