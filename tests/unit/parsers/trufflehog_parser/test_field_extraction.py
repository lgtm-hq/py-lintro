"""Unit tests for trufflehog parser field extraction."""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.parsers.trufflehog.trufflehog_parser import parse_trufflehog_output
from tests.unit.parsers.trufflehog_parser.conftest import make_finding


def test_parse_extracts_core_fields(single_finding_output: str) -> None:
    """Parser should extract file, line, detector, and verification fields.

    Args:
        single_finding_output: JSONL string with one finding.
    """
    issues = parse_trufflehog_output(output=single_finding_output)

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue.file).is_equal_to("config.py")
    assert_that(issue.line).is_equal_to(8)
    assert_that(issue.detector_name).is_equal_to("Github")
    assert_that(issue.detector_type).is_equal_to(8)
    assert_that(issue.decoder_name).is_equal_to("PLAIN")
    assert_that(issue.verified).is_false()
    assert_that(issue.source_name).is_equal_to("trufflehog - filesystem")


def test_parse_extracts_rotation_guide_from_extra_data() -> None:
    """Parser should surface the rotation guide from ExtraData."""
    output = json.dumps(
        make_finding(
            extra_data={
                "rotation_guide": "https://howtorotate.com/docs/tutorials/github/",
                "version": "2",
            },
        ),
    )

    issues = parse_trufflehog_output(output=output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].rotation_guide).is_equal_to(
        "https://howtorotate.com/docs/tutorials/github/",
    )
    assert_that(issues[0].extra_data).contains_key("version")


def test_parse_multiple_findings_jsonl() -> None:
    """Parser should handle multiple newline-delimited findings."""
    line_one = json.dumps(make_finding(file="a.py", line=1, detector_name="Github"))
    line_two = json.dumps(
        make_finding(file="b.py", line=2, detector_name="AWS", detector_type=2),
    )
    output = f"{line_one}\n{line_two}\n"

    issues = parse_trufflehog_output(output=output)

    assert_that(issues).is_length(2)
    assert_that(issues[0].file).is_equal_to("a.py")
    assert_that(issues[0].detector_name).is_equal_to("Github")
    assert_that(issues[1].file).is_equal_to("b.py")
    assert_that(issues[1].detector_name).is_equal_to("AWS")


def test_parse_verified_finding() -> None:
    """Parser should record verified=True for a verified finding."""
    output = json.dumps(make_finding(verified=True))

    issues = parse_trufflehog_output(output=output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].verified).is_true()


def test_parse_git_source_metadata() -> None:
    """Parser should fall back to non-filesystem sources that expose a file."""
    finding: dict[str, object] = {
        "SourceMetadata": {
            "Data": {"Git": {"file": "secret.py", "line": 42, "commit": "abc123"}},
        },
        "DetectorName": "Github",
        "DetectorType": 8,
        "Verified": False,
        "Raw": "ghp_examplefakeexamplefakeexamplefake1234",
    }

    issues = parse_trufflehog_output(output=json.dumps(finding))

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("secret.py")
    assert_that(issues[0].line).is_equal_to(42)
