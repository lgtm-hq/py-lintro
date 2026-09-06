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

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.golangci_lint.golangci_lint_issue import GolangciLintIssue
from lintro.tools.golangci_lint.definition import GolangciLintPlugin
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


def _diagnostics(result: ToolResult) -> str:
    """Render everything golangci-lint returned, for an assertion message.

    A missing sub-linter code means golangci-lint did not report the finding,
    which is invisible from the codes alone. #2391 spent three CI runs on that
    blind spot, so every failure now carries the tool-level fields plus the
    position-less ``(module)`` diagnostics golangci-lint uses for build,
    config, and typecheck failures.

    Args:
        result: The ToolResult returned by the golangci-lint plugin.

    Returns:
        A multi-line description of the run's outcome.
    """
    issues = cast(list[GolangciLintIssue], result.issues or [])
    module_level = [issue for issue in issues if issue.file == "(module)"]
    lines = [
        f"success={result.success}",
        f"timed_out={result.timed_out}",
        f"issues_count={result.issues_count}",
        f"codes={sorted({issue.code for issue in issues})}",
        f"raw output={result.output!r}",
    ]
    if module_level:
        lines.append("(module)-level diagnostics:")
        lines.extend(f"  [{issue.code}] {issue.message}" for issue in module_level)
    return "\n".join(lines)


def test_fixture_exists() -> None:
    """The committed Go fixture module is present."""
    assert_that((_FIXTURE / "go.mod").exists()).is_true()
    assert_that((_FIXTURE / "main.go").exists()).is_true()


def test_check_detects_violations(tmp_path: Path) -> None:
    """golangci-lint detects the seeded errcheck/ineffassign violations.

    This assertion went intermittently red in the Docker integration job
    (#2391). The cause was golangci-lint's exclusive start-up file lock: with
    the suite running under ``-n auto``, a second worker's instance exited 3
    with ``parallel golangci-lint is running`` and an empty ``Issues`` array,
    so the codes simply were not there. The plugin now passes
    ``--allow-parallel-runners``; the assertion itself is unchanged.

    Args:
        tmp_path: Pytest temporary directory holding the staged fixture.
    """
    module = _stage_fixture(tmp_path)
    plugin = GolangciLintPlugin()
    result = plugin.check([str(module)], {})
    diagnostics = _diagnostics(result)

    counted = assert_that(result.issues_count).described_as(diagnostics)
    counted.is_greater_than_or_equal_to(2)
    assert_that(result.success).described_as(diagnostics).is_false()
    assert_that(result.issues).described_as(diagnostics).is_not_none()
    issues = cast(list[GolangciLintIssue], result.issues)
    codes = {issue.code for issue in issues}
    assert_that(codes).described_as(diagnostics).contains("errcheck", "ineffassign")
    for issue in issues:
        assert_that(issue.file).described_as(diagnostics).is_not_empty()
        assert_that(issue.line).described_as(diagnostics).is_greater_than(0)


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
    diagnostics = _diagnostics(result)
    assert_that(result.issues).described_as(diagnostics).is_not_none()
    issues = cast(list[GolangciLintIssue], result.issues)
    codes = sorted({issue.code for issue in issues})

    # The fixture seeds these two sub-linters; asserting them by name keeps the
    # test meaningful whatever extra diagnostics a toolchain version adds.
    assert_that(codes).described_as(diagnostics).contains("errcheck", "ineffassign")
    for code in codes:
        if not code:
            # A finding with no originating sub-linter has no per-linter page.
            assert_that(plugin.doc_url(code)).is_none()
            continue
        assert_that(plugin.doc_url(code)).described_as(code).starts_with(
            "https://golangci-lint.run",
        )
