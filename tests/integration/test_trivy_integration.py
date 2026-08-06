"""Integration tests for Trivy (dependency-vulnerability scanner).

These tests require the ``trivy`` binary **and** a populated local vulnerability
database. Trivy runs with ``--skip-db-update`` (hermetic), so on a machine with
no cached DB it reports a non-fatal skip instead of scanning. The tests detect
that condition and skip rather than fail, so CI without a DB stays green.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.trivy.trivy_issue import TrivyIssue
from lintro.plugins import ToolRegistry

_DB_MISSING_MARKER = "vulnerability database not found"


@pytest.mark.skipif(
    shutil.which("trivy") is None,
    reason="Trivy not installed on PATH; skip integration test.",
)
def test_trivy_detects_vulnerabilities_on_sample_file() -> None:
    """Run Trivy against a seeded requirements.txt and expect findings."""
    tool = ToolRegistry.get("trivy")
    assert_that(tool).is_not_none()
    tool.exclude_patterns = []
    sample = os.path.abspath(
        "test_samples/tools/security/trivy/requirements.txt",
    )
    assert_that(os.path.exists(sample)).is_true()

    result: ToolResult = tool.check([sample], {})
    assert_that(isinstance(result, ToolResult)).is_true()
    assert_that(result.name).is_equal_to("trivy")

    if result.output and _DB_MISSING_MARKER in result.output:
        pytest.skip("Trivy vulnerability DB not available; skipping.")

    assert_that(result.success).is_false()
    assert_that(result.issues_count > 0).is_true()

    issue = result.issues[0]
    assert_that(issue).is_instance_of(TrivyIssue)
    assert_that(issue.vuln_id).is_not_empty()
    assert_that(issue.pkg_name).is_not_empty()
    logger.info(f"[TEST] trivy found {result.issues_count} vulnerabilities")


@pytest.mark.skipif(
    shutil.which("trivy") is None,
    reason="Trivy not installed on PATH; skip integration test.",
)
def test_trivy_clean_on_nonvulnerable_lockfile() -> None:
    """Trivy handles a dependency-free lockfile cleanly.

    An empty ``requirements.txt`` (comment only) declares no packages, so Trivy
    deterministically finds zero vulnerabilities regardless of the DB contents —
    unlike any concrete pin, which could acquire a CVE over time.
    """
    tool = ToolRegistry.get("trivy")
    tool.exclude_patterns = []
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "requirements.txt")
        with open(path, "w") as f:
            f.write("# no dependencies declared\n")

        result: ToolResult = tool.check([path], {})
        assert_that(result.name).is_equal_to("trivy")

        if result.output and _DB_MISSING_MARKER in result.output:
            pytest.skip("Trivy vulnerability DB not available; skipping.")

        assert_that(result.issues_count).is_equal_to(0)
        assert_that(result.success).is_true()
