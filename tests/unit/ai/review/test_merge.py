"""Tests for review result merge helpers."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.models.checklist_answer import ChecklistAnswer
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.orchestrator import merge_checklist_answers, merge_findings


def test_merge_findings_deduplicates_by_file_line_title() -> None:
    """Duplicate findings with same file, line, and title are merged once."""
    finding = ReviewFinding(
        severity="P2",
        category="logic-bug",
        file="src/main.py",
        line=10,
        title="Duplicate title",
        description="desc",
        cause="cause",
        fix="fix",
        confidence="high",
        checklist_ids=(1,),
    )
    merged = merge_findings(
        findings_groups=[(finding,), (finding,)],
    )

    assert_that(merged).is_length(1)


def test_merge_checklist_answers_prefers_yes_over_no() -> None:
    """Checklist merge keeps yes answers when conflicting answers exist."""
    no_answer = ChecklistAnswer(id=1, answer="no", evidence="none")
    yes_answer = ChecklistAnswer(id=1, answer="yes", evidence="src/main.py:10")

    merged = merge_checklist_answers(
        checklist_groups=[(no_answer,), (yes_answer,)],
    )

    assert_that(merged).is_length(1)
    assert_that(merged[0].answer).is_equal_to("yes")
    assert_that(merged[0].evidence).contains("src/main.py")


def test_merge_checklist_answers_requires_evidence_for_yes() -> None:
    """Unsupported yes answers do not override evidence-backed no answers."""
    supported_no = ChecklistAnswer(id=1, answer="no", evidence="src/main.py:10")
    unsupported_yes = ChecklistAnswer(id=1, answer="yes", evidence="")

    merged = merge_checklist_answers(
        checklist_groups=[(supported_no,), (unsupported_yes,)],
    )

    assert_that(merged).is_length(1)
    assert_that(merged[0].answer).is_equal_to("no")
    assert_that(merged[0].evidence).contains("src/main.py")
