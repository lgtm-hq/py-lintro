"""Tests for the derived merge-readiness verdict (#1907)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.verdict import (
    VERDICT_LABELS,
    VERDICT_RUBRIC_FINE_PRINT,
    derive_readiness_verdict,
    resolve_bullet_finding,
    verdict_label,
)


def _finding(
    *,
    severity: Severity,
    file: str = "lintro/thing.py",
    line: int = 10,
    title: str = "Something is wrong",
) -> ReviewFinding:
    """Build a finding with the given severity and location.

    Args:
        severity: Severity to assign.
        file: Repository-relative path.
        line: Line number.
        title: Finding title.

    Returns:
        The constructed finding.
    """
    return ReviewFinding(
        severity=severity,
        category="logic-bug",
        file=file,
        line=line,
        title=title,
        description="description",
        cause="cause",
        fix="fix",
        confidence="high",
    )


@pytest.mark.parametrize(
    ("severities", "expected"),
    [
        ((Severity.P1, Severity.P2, Severity.P3), ReviewVerdict.BLOCKED),
        ((Severity.P2, Severity.P3), ReviewVerdict.CHANGES_REQUESTED),
        ((Severity.P3,), ReviewVerdict.NITS_ONLY),
        ((), ReviewVerdict.READY),
    ],
    ids=["open=p1", "open=p2", "open=p3", "open=none"],
)
def test_derive_readiness_verdict_covers_rubric(
    severities: tuple[Severity, ...],
    expected: ReviewVerdict,
) -> None:
    """Every rubric outcome is derived from the open finding severities."""
    findings = [_finding(severity=severity) for severity in severities]

    assert_that(derive_readiness_verdict(findings=findings)).is_equal_to(expected)


def test_derive_readiness_verdict_ignores_lower_severities_when_p1_open() -> None:
    """A single open P1 blocks regardless of how many lower findings exist."""
    findings = [_finding(severity=Severity.P3) for _ in range(5)]
    findings.append(_finding(severity=Severity.P1))

    assert_that(derive_readiness_verdict(findings=findings)).is_equal_to(
        ReviewVerdict.BLOCKED,
    )


def test_review_result_exposes_derived_verdict() -> None:
    """ReviewResult derives its verdict from its own findings."""
    result = ReviewResult(
        metadata=ReviewMetadata(
            model="m",
            provider="p",
            context_window=1,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=1,
            files_total=1,
            checklist_items=1,
        ),
        summary="A change.",
        findings=(_finding(severity=Severity.P2),),
    )

    assert_that(result.readiness_verdict).is_equal_to(
        ReviewVerdict.CHANGES_REQUESTED,
    )


def test_verdict_labels_cover_every_member() -> None:
    """Every verdict member has a display label."""
    for verdict in ReviewVerdict:
        assert_that(verdict_label(verdict=verdict)).is_equal_to(VERDICT_LABELS[verdict])


def test_verdict_rubric_fine_print_names_every_label() -> None:
    """The rendered rubric fine-print states each verdict label."""
    for label in VERDICT_LABELS.values():
        assert_that(VERDICT_RUBRIC_FINE_PRINT).contains(label)


def test_resolve_bullet_finding_matches_file_and_line() -> None:
    """An exact file:line reference resolves to that finding."""
    target = _finding(severity=Severity.P1, file="a.py", line=42)
    other = _finding(severity=Severity.P3, file="a.py", line=7)

    resolved = resolve_bullet_finding(finding_ref="a.py:42", findings=[other, target])

    assert_that(resolved).is_equal_to(target)


def test_resolve_bullet_finding_falls_back_to_same_file() -> None:
    """A reference with a stale line still resolves within the same file."""
    finding = _finding(severity=Severity.P2, file="a.py", line=7)

    resolved = resolve_bullet_finding(finding_ref="a.py:999", findings=[finding])

    assert_that(resolved).is_equal_to(finding)


@pytest.mark.parametrize(
    "finding_ref",
    ["", "   ", "unknown.py:1"],
    ids=["ref=empty", "ref=blank", "ref=unreviewed-file"],
)
def test_resolve_bullet_finding_returns_none_when_unresolvable(
    finding_ref: str,
) -> None:
    """Unresolvable references yield no finding instead of raising."""
    finding = _finding(severity=Severity.P2, file="a.py", line=7)

    assert_that(
        resolve_bullet_finding(finding_ref=finding_ref, findings=[finding]),
    ).is_none()


def test_resolve_bullet_finding_falls_back_on_malformed_line_suffix() -> None:
    """A malformed (non-integer) line suffix still resolves via same-file fallback.

    Regression guard: the ValueError handler for an unparsable line suffix
    must reset only the line, not overwrite the already-correctly-split path
    with the whole unsplit reference — doing so would make the same-file
    fallback below it unreachable.
    """
    finding = _finding(severity=Severity.P2, file="a.py", line=7)

    resolved = resolve_bullet_finding(
        finding_ref="a.py:not_a_number",
        findings=[finding],
    )

    assert_that(resolved).is_equal_to(finding)


def test_questions_never_move_the_verdict() -> None:
    """A P1-labelled question leaves the verdict at the findings' level (#1925)."""
    question = replace(
        _finding(severity=Severity.P1, file="a.py", line=1),
        kind=FindingKind.QUESTION,
    )
    finding = _finding(severity=Severity.P3, file="b.py", line=2)

    verdict = derive_readiness_verdict(findings=[question, finding])

    assert_that(verdict).is_equal_to(ReviewVerdict.NITS_ONLY)


def test_a_review_of_only_questions_is_ready() -> None:
    """Questions alone leave nothing open to block the merge."""
    question = replace(
        _finding(severity=Severity.P1, file="a.py", line=1),
        kind=FindingKind.QUESTION,
    )

    assert_that(derive_readiness_verdict(findings=[question])).is_equal_to(
        ReviewVerdict.READY,
    )


def test_summary_bullets_never_resolve_to_a_question() -> None:
    """A severity-marked bullet must point at a severity'd finding."""
    question = replace(
        _finding(severity=Severity.P1, file="a.py", line=7),
        kind=FindingKind.QUESTION,
    )

    assert_that(
        resolve_bullet_finding(finding_ref="a.py:7", findings=[question]),
    ).is_none()


def test_exact_question_reference_does_not_fall_back_to_another_finding() -> None:
    """An exact reference to a question line never resolves via same-file fallback.

    ``a.py:7`` is a question and ``a.py:42`` is a real finding. A bullet that
    references the question's own line must yield ``None`` rather than
    silently attaching the question's severity to the unrelated finding at
    ``a.py:42`` through the same-file fallback.
    """
    question = replace(
        _finding(severity=Severity.P1, file="a.py", line=7),
        kind=FindingKind.QUESTION,
    )
    other = _finding(severity=Severity.P2, file="a.py", line=42)

    assert_that(
        resolve_bullet_finding(finding_ref="a.py:7", findings=[question, other]),
    ).is_none()
