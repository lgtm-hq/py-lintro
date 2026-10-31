"""Tests for the shared "did this run actually measure anything" predicate."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.tools.pylint.definition import (
    PYLINT_NO_INCLUDED_FILES,
    PYLINT_NOTHING_TO_LINT,
)
from lintro.utils.meaningful_run import (
    baseline_is_eligible,
    result_inspected_files,
    run_has_unreported_failure,
    run_inspected_files,
)


def _real_result() -> ToolResult:
    """Build a result from a tool that genuinely inspected files.

    Returns:
        ToolResult: A completed, non-skipped result with ordinary output.
    """
    return ToolResult(
        name="ruff",
        success=True,
        skipped=False,
        output="All checks passed",
        inspected_files=True,
    )


@pytest.mark.parametrize(
    "output",
    [
        "No files found to check.",
        "No Astro files to check.",
        "No .py/.pyi files found to check.",
        "no FILES found TO CHECK",
    ],
    ids=["generic", "astro", "python", "mixed-case"],
)
def test_result_inspected_files_rejects_empty_file_sets(output: str) -> None:
    """A tool that matched nothing is not evidence of a measurement.

    Such a tool still returns ``skipped=False`` and ``success=True``, so the
    producer supplies the no-files signal independently of the message.

    Args:
        output: A real "nothing to check" message from a wrapped tool.
    """
    result = ToolResult(name="ruff", success=True, output=output, no_files=True)

    assert_that(result_inspected_files(result)).is_false()


def test_result_inspected_files_rejects_a_skipped_tool() -> None:
    """A skipped tool measured nothing."""
    result = ToolResult(
        name="hadolint",
        success=True,
        skipped=True,
        skip_reason="hadolint not found",
    )

    assert_that(result_inspected_files(result)).is_false()


def test_result_inspected_files_rejects_a_timed_out_tool() -> None:
    """A timed-out tool's findings were never collected."""
    result = ToolResult(
        name="semgrep",
        success=False,
        skipped=False,
        timed_out=True,
        output="timed out after 300s",
    )

    assert_that(result_inspected_files(result)).is_false()


def test_result_inspected_files_accepts_a_real_run() -> None:
    """An ordinary completed tool counts as a measurement."""
    assert_that(result_inspected_files(_real_result())).is_true()


def test_run_inspected_files_needs_only_one_real_tool() -> None:
    """One real tool is enough, even beside skipped placeholders."""
    results = [
        ToolResult(
            name="hadolint",
            success=True,
            skipped=True,
            skip_reason="hadolint not found",
        ),
        _real_result(),
    ]

    assert_that(run_inspected_files(results)).is_true()


@pytest.mark.parametrize(
    ("success", "issues_count", "parsed_findings", "skipped", "expected"),
    [
        (False, 0, False, False, True),
        (False, 3, False, False, True),
        (False, 1, True, False, False),
        (False, 0, True, False, False),
        (True, 0, False, False, False),
        (False, 0, False, True, False),
    ],
    ids=[
        "unreported-failure",
        "gate-failure",
        "findings",
        "parsed-only",
        "clean",
        "skipped",
    ],
)
def test_run_has_unreported_failure(
    success: bool,
    issues_count: int,
    parsed_findings: bool,
    skipped: bool,
    expected: bool,
) -> None:
    """Only a non-skipped failure without findings disqualifies counts.

    Args:
        success: Whether the tool succeeded.
        issues_count: Number of findings reported by the tool.
        parsed_findings: Whether the tool supplied parsed findings.
        skipped: Whether the tool was skipped.
        expected: Whether the run has an unreported failure.
    """
    result = ToolResult(
        name="pylint",
        success=success,
        issues_count=issues_count,
        issues=(
            [RuffIssue(file="a.py", line=1, code="F401", message="unused")]
            if parsed_findings
            else []
        ),
        skipped=skipped,
        skip_reason="not installed" if skipped else None,
        inspected_files=True,
    )

    assert_that(run_has_unreported_failure([_real_result(), result])).is_equal_to(
        expected,
    )
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=[_real_result(), result],
        ),
    ).is_equal_to(not expected)


def test_run_has_unreported_failure_accepts_an_empty_run() -> None:
    """An empty run has no failed tool, even though it cannot publish counts."""
    assert_that(run_has_unreported_failure([])).is_false()


def test_run_inspected_files_rejects_an_all_skipped_run() -> None:
    """Skipped placeholders are results, but they are not measurements."""
    results = [
        ToolResult(
            name="hadolint",
            success=True,
            skipped=True,
            skip_reason="hadolint not found",
        ),
    ]

    assert_that(run_inspected_files(results)).is_false()


def test_baseline_is_eligible_for_a_real_check() -> None:
    """A plain check that inspected files is a comparable measurement."""
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=[_real_result()],
        ),
    ).is_true()


@pytest.mark.parametrize("coverage", [True, False, None])
def test_mixed_baseline_requires_known_coverage(coverage: bool | None) -> None:
    """Publication needs one inspection; comparison also rejects unknown peers.

    Args:
        coverage: A second producer's inspection state.
    """
    results = [
        _real_result(),
        ToolResult(name="peer", success=True, inspected_files=coverage),
    ]
    assert_that(run_inspected_files(results)).is_true()
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=results,
        ),
    ).is_equal_to(coverage is not None)


@pytest.mark.parametrize(
    ("action", "dry_run_preview", "results", "early_exit"),
    [
        (Action.FIX, False, [_real_result()], False),
        (Action.TEST, False, [_real_result()], False),
        (Action.CHECK, True, [_real_result()], False),
        (Action.CHECK, False, [], False),
        (Action.CHECK, False, [_real_result()], True),
        (
            Action.CHECK,
            False,
            [
                ToolResult(
                    name="hadolint",
                    success=True,
                    skipped=True,
                    skip_reason="hadolint not found",
                ),
            ],
            False,
        ),
    ],
    ids=[
        "fmt",
        "test",
        "dry-run-preview",
        "empty",
        "early-exit",
        "all-skipped",
    ],
)
def test_baseline_is_not_eligible_for_an_unmeasured_run(
    action: Action,
    dry_run_preview: bool,
    results: list[ToolResult],
    early_exit: bool,
) -> None:
    """Every shape that measured a different population is refused.

    Args:
        action: The action the run executed.
        dry_run_preview: Whether this is a ``fmt --dry-run`` preview.
        results: The run's tool results.
        early_exit: Whether the run stopped before executing any tool.
    """
    assert_that(
        baseline_is_eligible(
            action=action,
            dry_run_preview=dry_run_preview,
            tool_results=results,
            early_exit=early_exit,
        ),
    ).is_false()


@pytest.mark.parametrize(
    "output",
    [
        "No paths to check.",
        "No Cargo.lock found; skipping cargo-audit.",
        "No go.mod found; skipping golangci-lint.",
        "No auditable requirements or project file among the matched paths; skipping pip-audit.",
        "No import-linter configuration found; skipping.",
        PYLINT_NO_INCLUDED_FILES,
        PYLINT_NOTHING_TO_LINT,
        "No .proto files to format.",
        "No Go files found to fix.",
        "No .py/.pyi files found",
    ],
    ids=[
        "osv-scanner-paths",
        "cargo-audit",
        "golangci-lint",
        "pip-audit",
        "import-linter",
        "pylint-include",
        "pylint-to-lint",
        "to-format",
        "to-fix",
        "files-found",
    ],
)
def test_result_inspected_files_rejects_every_real_no_work_message(
    output: str,
) -> None:
    """A producer's no-inspection signal covers empty discovery and declines.

    Args:
        output: A real no-work message from a wrapped tool.
    """
    result = ToolResult(
        name="tool",
        success=True,
        output=output,
        inspected_files=False,
    )

    assert_that(result_inspected_files(result)).is_false()


@pytest.mark.parametrize(
    "output",
    [
        "No issues found",
        "No typos found.",
        "No fixes needed.",
        "No fixes applied.",
        "No conflicting settings between lintro and native configs",
    ],
    ids=["issues", "typos", "fixes-needed", "fixes-applied", "conflicts"],
)
def test_result_inspected_files_keeps_clean_pass_messages(output: str) -> None:
    """A clean pass also starts with "No", and must not be mistaken for no work.

    The producer records the inspection independently of its human message.

    Args:
        output: A real clean-pass message from a wrapped tool.
    """
    result = ToolResult(
        name="tool",
        success=True,
        output=output,
        inspected_files=True,
    )

    assert_that(result_inspected_files(result)).is_true()


def test_result_inspected_files_ignores_formatted_output() -> None:
    """Display text cannot override an explicit inspection signal."""
    result = ToolResult(
        name="tool",
        success=True,
        skipped=False,
        output=None,
        formatted_output="No .rs files found to check.",
        inspected_files=True,
    )

    assert_that(result_inspected_files(result)).is_true()


@pytest.mark.parametrize(
    "output",
    [
        None,
        "All checks passed",
        "No files to check.",
        PYLINT_NOTHING_TO_LINT,
        "Skipping stylelint: no configuration found",
    ],
)
@pytest.mark.parametrize("message_field", ["output", "formatted_output"])
def test_legacy_results_remain_unknown(
    output: str | None,
    message_field: str,
) -> None:
    """Neither clean nor no-work prose establishes a legacy measurement.

    Args:
        output: A message from an unconverted producer.
        message_field: The display field carrying the message.
    """
    result = ToolResult(name="legacy", success=True)
    setattr(result, message_field, output)

    assert_that(result_inspected_files(result)).is_none()
    assert_that(run_inspected_files([result])).is_false()
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=[result],
        ),
    ).is_false()


def test_the_no_files_flag_beats_an_output_no_heuristic_can_read() -> None:
    """Bandit is the case the prose heuristic could never decide.

    It nulls its ``output`` for the no-files result, making it byte-identical
    to a clean ruff pass. The structured flag says which is which, so the
    badge and the severity baseline stop treating "bandit examined nothing"
    as a measured clean run.
    """
    bandit = ToolResult(
        name="bandit",
        success=True,
        skipped=False,
        output=None,
        issues_count=0,
        no_files=True,
    )

    assert_that(result_inspected_files(bandit)).is_false()
    assert_that(run_inspected_files([bandit])).is_false()
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=[bandit],
        ),
    ).is_false()


def test_a_flagged_result_is_rejected_even_with_a_clean_looking_message() -> None:
    """The flag is read before the text, not after it.

    A converted producer that says something the heuristic would pass ("No
    issues found.", which means the tool *did* look) must still be classified
    as having examined nothing when it says so structurally.
    """
    result = ToolResult(
        name="osv-scanner",
        success=True,
        skipped=False,
        output="No issues found.",
        issues_count=0,
        no_files=True,
    )

    assert_that(result_inspected_files(result)).is_false()


def test_result_inspected_files_keeps_findings_mentioning_skipping() -> None:
    """A findings blob mentioning "Skipping" is not a declined-to-run result.

    The explicit inspection signal is unchanged by words in the report.
    """
    result = ToolResult(
        name="ruff",
        success=False,
        skipped=False,
        issues_count=1,
        output="a.py:1:1 F401 unused import\nSkipping the rest of the file",
        inspected_files=True,
    )

    assert_that(result_inspected_files(result)).is_true()
