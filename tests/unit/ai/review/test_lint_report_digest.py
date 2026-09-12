"""``--lint-report`` digest resolution for the review prompt (#2571)."""

from __future__ import annotations

import json
from pathlib import Path

from assertpy import assert_that

from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.preparation_resolvers import (
    LINT_FACTS_UNAVAILABLE,
    build_lint_digest_from_report,
)


def _context() -> ReviewContext:
    """Build a review context over one changed file.

    Returns:
        A context whose only changed file is ``src/main.py``.
    """
    return ReviewContext(
        base_ref="main",
        head_ref="abc123",
        changed_files=[
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff="",
    )


def test_report_digest_is_restricted_to_the_changed_files(tmp_path: Path) -> None:
    """Only findings on the review's changed files reach the prompt."""
    report = tmp_path / "results.json"
    report.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "tool": "ruff",
                        "issues": [
                            {
                                "file": "src/main.py",
                                "line": 1,
                                "code": "F401",
                                "message": "in",
                            },
                            {
                                "file": "src/other.py",
                                "line": 2,
                                "code": "E501",
                                "message": "out",
                            },
                        ],
                    },
                    {"tool": "black", "issues": []},
                ],
            },
        ),
        encoding="utf-8",
    )

    digest, tools, issues, note = build_lint_digest_from_report(
        context=_context(),
        report_path=report,
    )

    assert_that(digest).is_not_none()
    assert_that(digest).contains("<lint_results>")
    assert_that(digest).contains("src/main.py")
    assert_that(digest).does_not_contain("src/other.py")
    assert_that(tools).is_equal_to(2)
    assert_that(issues).is_equal_to(1)
    assert_that(note).is_empty()


def test_unusable_report_yields_a_header_note_not_an_error(tmp_path: Path) -> None:
    """A missing or malformed report degrades to a note; the review still runs."""
    digest, tools, issues, note = build_lint_digest_from_report(
        context=_context(),
        report_path=tmp_path / "absent.json",
    )

    assert_that(digest).is_none()
    assert_that((tools, issues)).is_equal_to((0, 0))
    assert_that(note).starts_with(LINT_FACTS_UNAVAILABLE)
    assert_that(note).contains("absent.json")


def test_missing_report_reason_becomes_the_header_note() -> None:
    """``--lint-report-missing`` is a request field that feeds the header note."""
    import dataclasses

    from lintro.ai.review.preparation import ReviewRunRequest
    from lintro.ai.review.preparation_resolvers import LINT_FACTS_UNAVAILABLE

    fields = {f.name: f for f in dataclasses.fields(ReviewRunRequest)}

    assert_that(fields).contains_key("lint_report_missing")
    assert_that(fields["lint_report_missing"].default).is_none()
    assert_that(LINT_FACTS_UNAVAILABLE).is_equal_to(
        "linter facts unavailable for this head",
    )
