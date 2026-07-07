"""Tests for the idiom-review issue dataclass and response parser."""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.idiom_review.idiom_review_issue import IdiomReviewIssue
from lintro.parsers.idiom_review.idiom_review_parser import IdiomReviewParser


def test_issue_default_field_values() -> None:
    """IdiomReviewIssue exposes the documented defaults."""
    issue = IdiomReviewIssue()

    assert_that(issue.code).is_equal_to("")
    assert_that(issue.severity).is_equal_to("WARNING")
    assert_that(issue.end_line).is_equal_to(0)
    assert_that(issue.confidence).is_equal_to("medium")
    assert_that(issue.suggested_idiom).is_equal_to("")


def test_display_field_map_includes_code_and_severity() -> None:
    """The display map renders the idiom code and severity."""
    field_map = IdiomReviewIssue.DISPLAY_FIELD_MAP

    assert_that(field_map).contains_key("code")
    assert_that(field_map).contains_key("severity")
    assert_that(field_map["code"]).is_equal_to("code")
    assert_that(field_map["severity"]).is_equal_to("severity")


def test_issue_to_display_row_uses_confidence_severity() -> None:
    """A parsed issue renders its severity through the base formatter."""
    issue = IdiomReviewIssue(
        file="a.py",
        line=3,
        message="Prefer any()",
        code="idiom/python/prefer-any",
        severity="WARNING",
    )
    row = issue.to_display_row()

    assert_that(row["code"]).is_equal_to("idiom/python/prefer-any")
    assert_that(row["severity"]).is_equal_to(str(SeverityLevel.WARNING))


def test_parse_file_review_valid_json() -> None:
    """A well-formed response yields one issue per finding."""
    response = json.dumps(
        {
            "findings": [
                {
                    "code": "idiom/python/prefer-any",
                    "line": 10,
                    "end_line": 14,
                    "message": "Replace the loop with any().",
                    "confidence": "high",
                    "suggested_idiom": "any(cond for x in items)",
                },
            ],
        },
    )
    issues = IdiomReviewParser().parse_file_review(response, "src/mod.py")

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue.file).is_equal_to("src/mod.py")
    assert_that(issue.line).is_equal_to(10)
    assert_that(issue.end_line).is_equal_to(14)
    assert_that(issue.code).is_equal_to("idiom/python/prefer-any")
    assert_that(issue.confidence).is_equal_to("high")
    assert_that(issue.severity).is_equal_to("WARNING")
    assert_that(issue.suggested_idiom).contains("any(")


def test_parse_file_review_markdown_fence() -> None:
    """JSON wrapped in a Markdown code fence is parsed."""
    payload = {
        "findings": [
            {
                "code": "idiom/python/prefer-get",
                "line": 4,
                "message": "Use dict.get().",
                "confidence": "medium",
            },
        ],
    }
    response = f"Here you go:\n```json\n{json.dumps(payload)}\n```\nThanks!"
    issues = IdiomReviewParser().parse_file_review(response, "m.py")

    assert_that(issues).is_length(1)
    assert_that(issues[0].severity).is_equal_to("INFO")
    # end_line defaults to the start line when omitted.
    assert_that(issues[0].end_line).is_equal_to(4)


def test_parse_file_review_confidence_severity_mapping() -> None:
    """Confidence maps to WARNING/INFO/HINT severities."""
    response = json.dumps(
        {
            "findings": [
                {"code": "a", "line": 1, "confidence": "high"},
                {"code": "b", "line": 2, "confidence": "medium"},
                {"code": "c", "line": 3, "confidence": "low"},
                {"code": "d", "line": 4, "confidence": "bogus"},
            ],
        },
    )
    issues = IdiomReviewParser().parse_file_review(response, "m.py")

    severities = [i.severity for i in issues]
    assert_that(severities).is_equal_to(["WARNING", "INFO", "HINT", "INFO"])


def test_parse_file_review_empty_response() -> None:
    """An empty response returns an empty list."""
    assert_that(IdiomReviewParser().parse_file_review("", "m.py")).is_empty()


def test_parse_file_review_malformed_json() -> None:
    """Truncated/malformed JSON returns an empty list, no exception."""
    truncated = '{"findings": [{"code": "x", "line": 1,'
    assert_that(
        IdiomReviewParser().parse_file_review(truncated, "m.py"),
    ).is_empty()


def test_parse_file_review_missing_findings_key() -> None:
    """A JSON object without 'findings' returns an empty list."""
    assert_that(
        IdiomReviewParser().parse_file_review('{"other": 1}', "m.py"),
    ).is_empty()


def test_parse_duplication_review_multi_location() -> None:
    """Each location in a duplicate group becomes one issue."""
    response = json.dumps(
        {
            "duplicate_groups": [
                {
                    "code": "idiom/cross-file/duplicate-slugify",
                    "message": "slugify reimplemented twice.",
                    "confidence": "high",
                    "suggested_idiom": "Extract to lintro/utils/text.py",
                    "locations": [
                        {"file": "a.py", "line": 5, "end_line": 9},
                        {"file": "b.py", "line": 20, "end_line": 24},
                    ],
                },
            ],
        },
    )
    issues = IdiomReviewParser().parse_duplication_review(response)

    assert_that(issues).is_length(2)
    assert_that({i.file for i in issues}).is_equal_to({"a.py", "b.py"})
    assert_that({i.code for i in issues}).is_equal_to(
        {"idiom/cross-file/duplicate-slugify"},
    )
    assert_that(issues[0].message).contains("slugify")
    assert_that(issues[0].severity).is_equal_to("WARNING")


def test_parse_duplication_review_empty_and_malformed() -> None:
    """Empty/malformed duplication responses return an empty list."""
    parser = IdiomReviewParser()

    assert_that(parser.parse_duplication_review("")).is_empty()
    assert_that(parser.parse_duplication_review("not json")).is_empty()
    assert_that(parser.parse_duplication_review('{"x": 1}')).is_empty()


def test_parse_duplication_review_skips_bad_locations() -> None:
    """Non-dict locations are skipped without raising."""
    response = json.dumps(
        {
            "duplicate_groups": [
                {
                    "code": "idiom/cross-file/duplicate-x",
                    "message": "dup",
                    "confidence": "medium",
                    "locations": ["not-a-dict", {"file": "c.py", "line": 1}],
                },
            ],
        },
    )
    issues = IdiomReviewParser().parse_duplication_review(response)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("c.py")


def test_parse_file_review_uses_json_after_explanatory_fence() -> None:
    """A prose fenced block before the JSON block does not break parsing."""
    payload = json.dumps(
        {
            "findings": [
                {
                    "code": "idiom/python/any-loop",
                    "line": 3,
                    "end_line": 5,
                    "message": "Use any() instead of a manual loop.",
                    "confidence": "high",
                    "suggested_idiom": "any(x for x in items)",
                },
            ],
        },
    )
    response = (
        "Here is an example of the verbose pattern:\n"
        "```python\nfor x in items:\n    if x:\n        found = True\n```\n"
        f"And the findings:\n```json\n{payload}\n```\n"
    )

    issues = IdiomReviewParser().parse_file_review(response, "pkg/mod.py")

    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("idiom/python/any-loop")
