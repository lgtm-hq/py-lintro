"""Context-file finding rejection and flagged_files parse (#2154)."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.finding_parser import (
    parse_flagged_files,
    reject_context_findings,
)
from lintro.ai.review.models.review_finding import ReviewFinding, Severity


def _finding(*, file: str, title: str = "Look again") -> ReviewFinding:
    """Build a finding pointed at ``file``."""
    return ReviewFinding(
        severity=Severity.P3,
        category="logic-bug",
        file=file,
        line=1,
        title=title,
        description="d",
        cause="c",
        fix="f",
        confidence="high",
    )


def test_findings_on_queue_files_are_kept() -> None:
    """Findings against files that needed review stay findings."""
    kept, flags = reject_context_findings(
        findings=(_finding(file="src/app.py"),),
        allowed_paths={"src/app.py"},
        eligible_paths={"src/app.py", "src/other.py"},
    )

    assert_that(kept).is_length(1)
    assert_that(flags).is_empty()


def test_findings_on_eligible_context_files_become_flags() -> None:
    """A finding on a read-only group-mate becomes a guarded re-read flag."""
    kept, flags = reject_context_findings(
        findings=(_finding(file="src/other.py", title="Contract changed"),),
        allowed_paths={"src/app.py"},
        eligible_paths={"src/app.py", "src/other.py"},
    )

    assert_that(kept).is_empty()
    assert_that(flags).is_length(1)
    assert_that(flags[0].path).is_equal_to("src/other.py")
    assert_that(flags[0].reason).is_equal_to("Contract changed")


def test_findings_on_unknown_paths_are_dropped() -> None:
    """A finding outside the diff is discarded, not flagged."""
    kept, flags = reject_context_findings(
        findings=(_finding(file="vendor/lib.py"),),
        allowed_paths={"src/app.py"},
        eligible_paths={"src/app.py"},
    )

    assert_that(kept).is_empty()
    assert_that(flags).is_empty()


def test_parse_flagged_files_requires_path_and_reason() -> None:
    """Empty or non-mapping entries are dropped."""
    flags = parse_flagged_files(
        raw_flags=[
            {"path": "src/app.py", "reason": "re-read imports"},
            {"path": "", "reason": "nope"},
            {"path": "src/app.py"},
            "ignore",
        ],
    )

    assert_that(flags).is_length(1)
    assert_that(flags[0].reason).is_equal_to("re-read imports")
