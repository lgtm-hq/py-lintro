"""Tests for review result merge helpers."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.merge import (
    merge_findings,
)
from lintro.ai.review.models.review_finding import ReviewFinding, Severity


def test_merge_findings_deduplicates_by_file_line_title() -> None:
    """Duplicate findings with same file, line, and title are merged once."""
    finding = ReviewFinding(
        severity=Severity.P2,
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
