"""Unit tests for the duplicate-code ratchet gate (issue #2293)."""

from __future__ import annotations

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.pylint.pylint_issue import PylintIssue
from lintro.utils.duplicate_code import (
    DUPLICATE_CODE_BASELINE_KEY,
    apply_duplicate_code_baseline,
    is_duplicate_code_issue,
    resolve_duplicate_code_baseline,
)


def _duplicate_issue(index: int) -> PylintIssue:
    """Build an R0801 issue standing in for one clone set.

    Args:
        index: Distinguishing index used in the file name.

    Returns:
        PylintIssue: A duplicate-code finding.
    """
    return PylintIssue(
        file=f"lintro/tools/definitions/tool_{index}.py",
        line=1,
        code="R0801",
        symbol="duplicate-code",
        message_type="refactor",
        message="Similar lines in 2 files",
    )


def _other_issue() -> PylintIssue:
    """Build a non-duplicate pylint issue.

    Returns:
        PylintIssue: A finding the gate must leave alone.
    """
    return PylintIssue(
        file="lintro/tools/definitions/tool_0.py",
        line=7,
        code="C0116",
        symbol="missing-function-docstring",
        message_type="convention",
        message="Missing function docstring",
    )


def _pylint_result(*issues: PylintIssue) -> ToolResult:
    """Build a pylint tool result carrying the given issues.

    Args:
        *issues: Issues the run reported.

    Returns:
        ToolResult: A result shaped like the pylint plugin's own output.
    """
    return ToolResult(
        name="pylint",
        success=not issues,
        issues_count=len(issues),
        issues=list(issues) or None,
    )


def test_resolve_baseline_reads_the_configured_integer() -> None:
    """A plain integer baseline is returned as configured."""
    baseline = resolve_duplicate_code_baseline(
        config={DUPLICATE_CODE_BASELINE_KEY: 34},
    )

    assert_that(baseline).is_equal_to(34)


def test_resolve_baseline_accepts_a_numeric_string() -> None:
    """A stringly-typed baseline is coerced rather than rejected."""
    baseline = resolve_duplicate_code_baseline(
        config={DUPLICATE_CODE_BASELINE_KEY: "12"},
    )

    assert_that(baseline).is_equal_to(12)


def test_resolve_baseline_disables_the_gate_when_unset() -> None:
    """A missing key leaves the gate unconfigured instead of defaulting to 0."""
    assert_that(resolve_duplicate_code_baseline(config={})).is_none()


def test_resolve_baseline_rejects_unusable_values() -> None:
    """Booleans, negatives and junk disable the gate rather than crashing it."""
    for value in (True, -1, "many", [3], None):
        assert_that(
            resolve_duplicate_code_baseline(
                config={DUPLICATE_CODE_BASELINE_KEY: value},
            ),
        ).is_none()


def test_is_duplicate_code_issue_matches_only_r0801() -> None:
    """Only the duplicate-code message id counts towards the gate."""
    assert_that(is_duplicate_code_issue(_duplicate_issue(1))).is_true()
    assert_that(is_duplicate_code_issue(_other_issue())).is_false()


def test_count_below_baseline_passes_and_strips_findings() -> None:
    """Fewer clones than the baseline pass, and pylint stops reporting them."""
    result = _pylint_result(_duplicate_issue(1), _duplicate_issue(2))

    verdict = apply_duplicate_code_baseline(results=[result], baseline=3)

    assert verdict is not None  # narrow type for mypy
    assert_that(verdict.count).is_equal_to(2)
    assert_that(verdict.exceeded).is_false()
    assert_that(verdict.message).contains("within baseline 3")
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.issues).is_none()
    assert_that(result.success).is_true()


def test_count_equal_to_baseline_passes() -> None:
    """A run sitting exactly on the baseline is green."""
    result = _pylint_result(_duplicate_issue(1), _duplicate_issue(2))

    verdict = apply_duplicate_code_baseline(results=[result], baseline=2)

    assert verdict is not None  # narrow type for mypy
    assert_that(verdict.exceeded).is_false()
    assert_that(result.success).is_true()


def test_count_above_baseline_is_a_failure() -> None:
    """One clone set more than the baseline fails with the ratchet message."""
    result = _pylint_result(_duplicate_issue(1), _duplicate_issue(2))

    verdict = apply_duplicate_code_baseline(results=[result], baseline=1)

    assert verdict is not None  # narrow type for mypy
    assert_that(verdict.count).is_equal_to(2)
    assert_that(verdict.exceeded).is_true()
    assert_that(verdict.message).is_equal_to(
        "duplicate-code count 2 exceeds baseline 1; baseline may only shrink",
    )


def test_non_duplicate_findings_are_left_alone() -> None:
    """Other pylint messages keep failing the run on their own terms."""
    result = _pylint_result(_duplicate_issue(1), _other_issue())

    verdict = apply_duplicate_code_baseline(results=[result], baseline=5)

    assert verdict is not None  # narrow type for mypy
    assert_that(verdict.count).is_equal_to(1)
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.success).is_false()
    assert result.issues is not None  # narrow type for mypy
    assert_that(result.issues[0].get_code()).is_equal_to("C0116")


def test_no_pylint_result_yields_no_verdict() -> None:
    """A run without pylint reports nothing rather than an empty pass."""
    other = ToolResult(name="ruff", success=True, issues_count=0)

    assert_that(
        apply_duplicate_code_baseline(results=[other], baseline=34),
    ).is_none()


def test_skipped_pylint_result_yields_no_verdict() -> None:
    """A skipped pylint run is not evidence that duplication went away."""
    skipped = ToolResult(
        name="pylint",
        skipped=True,
        skip_reason="pylint is not installed",
        issues_count=0,
    )

    assert_that(
        apply_duplicate_code_baseline(results=[skipped], baseline=34),
    ).is_none()
