"""Binary-gated integration tests for golangci-lint against a Go fixture module.

These tests run the real golangci-lint binary against the committed fixture at
``test_samples/tools/go/golangci_lint``. They skip when golangci-lint or the Go
toolchain is missing, and fail when either is missing inside the tools image.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import cast

from assertpy import assert_that

from lintro.parsers.golangci_lint.golangci_lint_issue import GolangciLintIssue
from lintro.tools.definitions.golangci_lint import GolangciLintPlugin
from tests.integration._tools import require_tool

_FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "test_samples"
    / "tools"
    / "go"
    / "golangci_lint"
)

# golangci-lint builds the module before linting, so both the linter and a Go
# toolchain must be present.
pytestmark = [
    require_tool("golangci-lint", version_args=("version",)),
    require_tool("go", version_args=("version",)),
]


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


def test_doc_url_resolves_for_every_detected_linter(tmp_path: Path) -> None:
    """Every sub-linter code the fixture produces resolves to a doc URL.

    Taking ``next(iter(codes))`` off a set picked a different code per
    process, because set iteration order follows the interpreter's hash seed.
    golangci-lint reports package-level build and config diagnostics with an
    empty ``FromLinter``, for which ``doc_url`` documents ``None``, so which
    code the assertion happened to draw decided whether the test passed
    (#2375). Checking the sorted codes removes the draw.

    Args:
        tmp_path: Pytest temporary directory holding the staged fixture.
    """
    module = _stage_fixture(tmp_path)
    plugin = GolangciLintPlugin()
    result = plugin.check([str(module)], {})
    assert_that(result.issues).is_not_none()
    issues = cast(list[GolangciLintIssue], result.issues)
    codes = sorted({issue.code for issue in issues})

    # The fixture seeds these two sub-linters; asserting them by name keeps the
    # test meaningful whatever extra diagnostics a toolchain version adds.
    assert_that(codes).contains("errcheck", "ineffassign")
    for code in codes:
        if not code:
            # A finding with no originating sub-linter has no per-linter page.
            assert_that(plugin.doc_url(code)).is_none()
            continue
        assert_that(plugin.doc_url(code)).described_as(code).starts_with(
            "https://golangci-lint.run",
        )
