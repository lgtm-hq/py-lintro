"""Unit tests for trufflehog parser edge cases."""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.parsers.trufflehog.trufflehog_parser import parse_trufflehog_output
from tests.unit.parsers.trufflehog_parser.conftest import make_finding


def test_log_lines_are_ignored(log_line: str) -> None:
    """Diagnostic log lines (no SourceMetadata) should be ignored.

    Args:
        log_line: A JSON log line without SourceMetadata.
    """
    finding = json.dumps(make_finding())
    output = f"{log_line}\n{finding}\n{log_line}\n"

    issues = parse_trufflehog_output(output=output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].detector_name).is_equal_to("Github")


def test_blank_lines_between_findings_are_skipped() -> None:
    """Blank lines interleaved with findings should be skipped."""
    finding = json.dumps(make_finding())
    output = f"\n{finding}\n\n"

    assert_that(parse_trufflehog_output(output=output)).is_length(1)


def test_finding_without_file_is_skipped() -> None:
    """A finding whose SourceMetadata has no file must be skipped."""
    finding: dict[str, object] = {
        "SourceMetadata": {"Data": {"Filesystem": {"line": 3}}},
        "DetectorName": "Github",
        "Verified": False,
    }

    assert_that(parse_trufflehog_output(output=json.dumps(finding))).is_empty()


def test_finding_with_missing_source_metadata_is_skipped() -> None:
    """A JSON object without SourceMetadata should be treated as non-finding."""
    output = json.dumps({"DetectorName": "Github", "Verified": True})

    assert_that(parse_trufflehog_output(output=output)).is_empty()


def test_redacted_only_secret_still_flags_redacted() -> None:
    """A finding with only a Redacted value should still show a REDACTED hint."""
    output = json.dumps(make_finding(raw="", redacted="ghp_****"))

    issues = parse_trufflehog_output(output=output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].message).contains("[REDACTED]")
