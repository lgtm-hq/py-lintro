"""Unit tests for the TrufflehogIssue model."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.trufflehog.trufflehog_issue import TrufflehogIssue


def test_trufflehog_issue_display_row() -> None:
    """TrufflehogIssue should produce a correct display row."""
    issue = TrufflehogIssue(  # nosec B106 - test data for secret detection
        file="config.py",
        line=8,
        column=0,
        detector_name="Github",
        detector_type=8,
        description="GitHub personal access token",
        verified=False,
        raw="ghp_examplefakeexamplefakeexamplefake1234",  # noqa: S106
    )

    row = issue.to_display_row()

    assert_that(row["file"]).is_equal_to("config.py")
    assert_that(row["line"]).is_equal_to("8")
    assert_that(row["code"]).is_equal_to("Github")
    assert_that(row["message"]).contains("GitHub personal access token")
    assert_that(row["message"]).contains("(unverified)")
    assert_that(row["message"]).contains("[REDACTED]")


def test_trufflehog_issue_verified_status_in_message() -> None:
    """A verified finding should surface a verified status in the message."""
    issue = TrufflehogIssue(  # nosec B106 - test data for secret detection
        file="config.py",
        line=1,
        detector_name="AWS",
        verified=True,
        raw="AKIAIOSFODNN7EXAMPLE",  # noqa: S106
    )

    assert_that(issue.message).contains("(verified)")
    assert_that(issue.message).does_not_contain("(unverified)")


def test_trufflehog_issue_message_without_secret() -> None:
    """The message should omit the REDACTED hint when no secret is present."""
    issue = TrufflehogIssue(
        file="test.py",
        line=1,
        detector_name="Github",
        description="Test detector",
        raw="",
    )

    assert_that(issue.message).is_equal_to("[Github] Test detector (unverified)")
    assert_that(issue.message).does_not_contain("REDACTED")
