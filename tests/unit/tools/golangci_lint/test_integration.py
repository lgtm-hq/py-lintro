"""Binary-gated integration tests for golangci-lint against a Go fixture module.

These tests run the real golangci-lint binary against the committed fixture at
``test_samples/tools/go/golangci_lint``. They are skipped when golangci-lint or
the Go toolchain is not installed.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import cast

import pytest
from assertpy import assert_that

from lintro.parsers.golangci_lint.golangci_lint_issue import GolangciLintIssue
from lintro.tools.definitions.golangci_lint import GolangciLintPlugin
from tests.unit.tools.golangci_lint.conftest import golangci_lint_available

_FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "test_samples"
    / "tools"
    / "go"
    / "golangci_lint"
)

pytestmark = pytest.mark.skipif(
    not golangci_lint_available(),
    reason="golangci-lint and/or the Go toolchain are not installed",
)


def _stage_fixture(tmp_path: Path) -> Path:
    """Copy the committed Go fixture into ``tmp_path``.

    The fixture lives under ``test_samples/`` which lintro's ``.lintro-ignore``
    excludes from file discovery, so it is copied into a temporary directory
    for the real check run.

    Args:
        tmp_path: Destination directory.

    Returns:
        Path to the staged fixture module.
    """
    dest = tmp_path / "golangci_lint"
    shutil.copytree(_FIXTURE, dest)
    return dest


def test_fixture_exists() -> None:
    """The committed Go fixture module is present."""
    assert_that((_FIXTURE / "go.mod").exists()).is_true()
    assert_that((_FIXTURE / "main.go").exists()).is_true()


def test_check_detects_violations(tmp_path: Path) -> None:
    """golangci-lint detects the seeded errcheck/ineffassign violations."""
    module = _stage_fixture(tmp_path)
    plugin = GolangciLintPlugin()
    result = plugin.check([str(module)], {})

    assert_that(result.issues_count).is_greater_than_or_equal_to(2)
    assert_that(result.success).is_false()
    assert_that(result.issues).is_not_none()
    issues = cast(list[GolangciLintIssue], result.issues)
    codes = {issue.code for issue in issues}
    assert_that(codes).contains("errcheck", "ineffassign")
    for issue in issues:
        assert_that(issue.file).is_not_empty()
        assert_that(issue.line).is_greater_than(0)


def test_doc_url_resolves_for_detected_linter(tmp_path: Path) -> None:
    """The plugin resolves a doc URL for a detected sub-linter code."""
    module = _stage_fixture(tmp_path)
    plugin = GolangciLintPlugin()
    result = plugin.check([str(module)], {})
    assert_that(result.issues).is_not_none()
    issues = cast(list[GolangciLintIssue], result.issues)
    codes = {issue.code for issue in issues}
    assert_that(plugin.doc_url(next(iter(codes)))).starts_with(
        "https://golangci-lint.run",
    )
