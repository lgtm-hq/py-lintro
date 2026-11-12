"""Findings from an incomplete stage survive retry and verification (#3352).

The first fix pass finds A. A retry fails (it times out or does not finish
executing) after reporting A again and a new finding B. The fix convergence
retry keeps both; the run-level verify fold must not then cut the findings
back to the pre-fix A, whether the verification is itself unknown or answers
only for some files.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.tools.core.verify_pass import (
    VerifyOutcome,
    VerifyScope,
    VerifyStatus,
    fold_verify_results,
)
from lintro.utils.execution.fix_retry import run_fix_with_retry

FINDING_A = ("/repo/a.py", 1, "finding A")
FINDING_B = ("/repo/b.py", 2, "finding B")


def _issue(identity: tuple[str, int, str]) -> BaseIssue:
    """Build a new finding object with the given identity.

    Args:
        identity: ``(file, line, message)``.

    Returns:
        A distinct issue object, so de-duplication must use identity.
    """
    file, line, message = identity
    return BaseIssue(file=file, line=line, message=message)


def _retried(*, execution_failed: bool, timed_out: bool) -> ToolResult:
    """Run a first pass finding A, then a failed retry reporting A and B.

    Args:
        execution_failed: Whether the retry failed to execute.
        timed_out: Whether the retry timed out.

    Returns:
        The convergence retry's merged result.
    """
    first_findings = [_issue(FINDING_A)]
    first = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=first_findings,
        initial_issues=first_findings,
        initial_issues_count=1,
        fixed_issues_count=0,
        remaining_issues_count=1,
    )
    failed = ToolResult(
        name="ruff",
        success=False,
        output="retry did not finish",
        issues_count=2,
        issues=[_issue(FINDING_A), _issue(FINDING_B)],
        execution_failed=execution_failed,
        timed_out=timed_out,
    )
    tool = MagicMock()
    tool.fix.side_effect = [first, failed]
    return run_fix_with_retry(tool, ["/repo"], {}, max_retries=3)


def _identities(result: ToolResult) -> list[tuple[str, int, str]]:
    """Return each finding's identity, in order.

    Args:
        result: A folded result.

    Returns:
        ``(file, line, message)`` per finding.
    """
    return [(issue.file, issue.line, issue.message) for issue in result.issues]


@pytest.mark.parametrize(
    ("execution_failed", "timed_out"),
    [(True, False), (False, True)],
    ids=["retry-execution-failed", "retry-timed-out"],
)
def test_failed_retry_findings_survive_a_verification_timeout(
    execution_failed: bool,
    timed_out: bool,
) -> None:
    """A then a failed retry with A+B then a verification timeout keeps A and B."""
    results = [_retried(execution_failed=execution_failed, timed_out=timed_out)]

    fold_verify_results(
        mutation_results=results,
        verify_results=[
            VerifyOutcome(
                tool="ruff",
                result=None,
                status=VerifyStatus.UNKNOWN,
                unknown_reason="check timed out",
            ),
        ],
        scope=VerifyScope(files=("/repo/a.py",), narrowed=True),
    )

    folded = results[0]
    assert_that(_identities(folded)).is_equal_to([FINDING_A, FINDING_B])
    assert_that(folded.issues_count).is_equal_to(2)
    assert_that(folded.success).is_false()
    assert_that(folded.residual_unknown).is_true()
    assert_that(folded.initial_issues_count).is_equal_to(1)
    assert_that(folded.fixed_issues_count).is_none()
    assert_that(folded.remaining_issues_count).is_none()


def test_answered_check_keeps_failed_retry_findings_it_did_not_examine() -> None:
    """A check that answers for a.py alone cannot drop B on b.py."""
    results = [_retried(execution_failed=True, timed_out=False)]
    clean_a = ToolResult(
        name="ruff",
        success=True,
        issues_count=0,
        inspected_files=True,
        findings_complete=True,
    )

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="ruff", result=clean_a)],
        scope=VerifyScope(files=("/repo/a.py",), narrowed=True),
    )

    folded = results[0]
    assert_that(_identities(folded)).is_equal_to([FINDING_B])
    assert_that(folded.success).is_false()
    assert_that(folded.execution_failed).is_true()
    assert_that(folded.remaining_issues_count).is_equal_to(1)


def test_unknown_verification_keeps_findings_its_partial_check_reported() -> None:
    """A check that timed out part-way still contributes what it found."""
    detected = [_issue(FINDING_A)]
    mutation = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=detected,
        initial_issues=detected,
        initial_issues_count=1,
        fixed_issues_count=0,
        remaining_issues_count=1,
    )
    partial = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[_issue(FINDING_B)],
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[
            VerifyOutcome(
                tool="ruff",
                result=partial,
                status=VerifyStatus.UNKNOWN,
                unknown_reason="check timed out",
            ),
        ],
        scope=VerifyScope(files=("/repo/a.py", "/repo/b.py"), narrowed=True),
    )

    folded = results[0]
    assert_that(sorted(_identities(folded))).is_equal_to([FINDING_A, FINDING_B])
    assert_that(folded.residual_unknown).is_true()
    assert_that(folded.fixed_issues_count).is_none()
    assert_that(folded.remaining_issues_count).is_none()
    assert_that(folded.output).contains("Usable findings from the reports")
