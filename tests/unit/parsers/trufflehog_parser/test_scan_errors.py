"""Unit tests for TruffleHog scan-error classification helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.trufflehog.trufflehog_errors import (
    extract_trufflehog_scan_errors,
    is_benign_missing_path_error,
    scan_errors_are_all_benign,
    stderr_reports_scan_errors,
)


def test_extract_errors_from_json_scan_log() -> None:
    """JSON scan-error logs should yield the per-path error strings."""
    stderr = (
        '{"level":"error","msg":"encountered errors during scan",'
        '"errors":["lstat /ci/coverage: no such file or directory",'
        '"open /secret: permission denied"]}'
    )

    errors = extract_trufflehog_scan_errors(stderr)

    assert_that(errors).is_equal_to(
        [
            "lstat /ci/coverage: no such file or directory",
            "open /secret: permission denied",
        ],
    )


def test_extract_errors_from_plain_lstat_lines() -> None:
    """Plain lstat lines should be collected when JSON is absent."""
    stderr = "lstat /ci/coverage: no such file or directory\n"

    errors = extract_trufflehog_scan_errors(stderr)

    assert_that(errors).is_equal_to(
        ["lstat /ci/coverage: no such file or directory"],
    )


def test_extract_retains_unclassifiable_banner_line() -> None:
    """A plain-text banner without reasons is retained so callers fail closed."""
    stderr = "level=error msg=encountered errors during scan"

    errors = extract_trufflehog_scan_errors(stderr)

    assert_that(errors).is_equal_to([stderr])
    assert_that(
        scan_errors_are_all_benign(errors, scan_paths={"/repo/src/a.py"}),
    ).is_false()


def test_extract_returns_empty_for_blank_stderr() -> None:
    """Whitespace-only stderr yields nothing, which the caller treats as unsafe."""
    errors = extract_trufflehog_scan_errors("   \n\t\n")

    assert_that(errors).is_empty()
    assert_that(
        scan_errors_are_all_benign(errors, scan_paths={"/repo/src/a.py"}),
    ).is_false()


def test_extract_retains_unclassified_line_alongside_benign_one() -> None:
    """An unrecognised error must survive next to a benign missing-path error."""
    stderr = (
        "lstat /ci/coverage: no such file or directory\n"
        "failed to open archive /repo/src/big.zip: unexpected EOF\n"
    )

    errors = extract_trufflehog_scan_errors(stderr)

    assert_that(errors).is_equal_to(
        [
            "lstat /ci/coverage: no such file or directory",
            "failed to open archive /repo/src/big.zip: unexpected EOF",
        ],
    )
    assert_that(
        scan_errors_are_all_benign(errors, scan_paths={"/repo/src/a.py"}),
    ).is_false()


def test_extract_deduplicates_repeated_unclassified_lines() -> None:
    """Repeated unclassified lines are recorded once."""
    stderr = "weird failure\nweird failure\n"

    assert_that(extract_trufflehog_scan_errors(stderr)).is_equal_to(
        ["weird failure"],
    )


def test_extract_ignores_routine_json_log_records() -> None:
    """Non-error JSON log records are progress noise, not scan errors."""
    stderr = (
        '{"level":"info-0","msg":"running source","source_manager_worker_id":"x"}\n'
        '{"level":"error","msg":"encountered errors during scan",'
        '"errors":["lstat /ci/coverage: no such file or directory"]}\n'
        '{"level":"info-0","msg":"finished scanning","chunks":1}\n'
    )

    errors = extract_trufflehog_scan_errors(stderr)

    assert_that(errors).is_equal_to(
        ["lstat /ci/coverage: no such file or directory"],
    )
    assert_that(
        scan_errors_are_all_benign(errors, scan_paths={"/repo/src/a.py"}),
    ).is_true()


def test_json_error_record_beside_benign_aggregate_is_retained() -> None:
    """A separate JSON error record must not be lost behind a benign aggregate.

    This is the exact shape TruffleHog emits when one target is unreadable and
    another is a missing CI-only path: an ``error``-level record carrying an
    ``error`` field, plus the aggregate payload.
    """
    stderr = (
        '{"level":"info-0","msg":"running source"}\n'
        '{"level":"error","msg":"error scanning file","path":"/repo/secret.txt",'
        '"error":"unable to open file: open /repo/secret.txt: permission denied"}\n'
        '{"level":"error","msg":"encountered errors during scan",'
        '"errors":["lstat /ci/coverage: no such file or directory"]}\n'
    )

    errors = extract_trufflehog_scan_errors(stderr)

    assert_that(errors).contains(
        "unable to open file: open /repo/secret.txt: permission denied",
    )
    assert_that(
        scan_errors_are_all_benign(errors, scan_paths={"/repo/src/a.py"}),
    ).is_false()


@pytest.mark.parametrize(
    "record",
    [
        '{"level":"error","msg":"failed to open archive","path":"/repo/x.zip"}',
        '{"level":"fatal","msg":"scanner aborted"}',
        '{"level":"panic","msg":"boom"}',
        '{"level":"info-0","msg":"chunk skipped","error":"read: broken pipe"}',
    ],
    ids=["error-level", "fatal-level", "panic-level", "info-with-error-field"],
)
def test_json_error_records_are_retained(record: str) -> None:
    """Error-severity JSON records are retained regardless of their message.

    Args:
        record: A single JSON log record that signals a failure.
    """
    errors = extract_trufflehog_scan_errors(record)

    assert_that(errors).is_length(1)
    assert_that(
        scan_errors_are_all_benign(errors, scan_paths={"/repo/src/a.py"}),
    ).is_false()


def test_json_error_record_without_reason_keeps_raw_line() -> None:
    """An error record with no reason text retains the raw line verbatim."""
    record = '{"level":"error","msg":"failed to open archive","path":"/repo/x.zip"}'

    assert_that(extract_trufflehog_scan_errors(record)).is_equal_to([record])


@pytest.mark.parametrize(
    "record",
    [
        '{"level":"warn","msg":"detector deprecated"}',
        '{"level":"debug-2","msg":"chunk emitted","chunks":3}',
        '{"level":"info-0","msg":"running source"}',
    ],
    ids=["warn-advisory", "debug-progress", "info-progress"],
)
def test_json_advisory_records_are_dropped(record: str) -> None:
    """Info/debug/warn records with no error field are routine noise.

    Args:
        record: A single benign-advisory JSON log record.
    """
    assert_that(extract_trufflehog_scan_errors(record)).is_empty()


@pytest.mark.parametrize(
    "record",
    [
        '{"error":"read: broken pipe"}',
        '{"unexpected":"structure","no":"level"}',
        '{"level":"trace","msg":"unknown level stem"}',
    ],
    ids=["error-field-no-level", "no-level-at-all", "unrecognised-level"],
)
def test_unclassifiable_json_records_are_retained(record: str) -> None:
    """A JSON record we cannot prove benign is kept so the caller fails closed.

    Args:
        record: A single JSON record with no recognised benign level.
    """
    errors = extract_trufflehog_scan_errors(record)

    assert_that(errors).is_length(1)
    assert_that(
        scan_errors_are_all_benign(errors, scan_paths={"/repo/src/a.py"}),
    ).is_false()


def test_aggregate_payload_takes_precedence_over_raw_retention() -> None:
    """The aggregate payload expands to reasons rather than the raw line."""
    stderr = (
        '{"level":"error","msg":"encountered errors during scan",'
        '"errors":["lstat /ci/coverage: no such file or directory"]}'
    )

    assert_that(extract_trufflehog_scan_errors(stderr)).is_equal_to(
        ["lstat /ci/coverage: no such file or directory"],
    )


def test_aggregate_payload_with_empty_errors_fails_closed() -> None:
    """An aggregate payload with no usable reasons is still retained."""
    stderr = '{"level":"error","msg":"encountered errors during scan","errors":[]}'

    errors = extract_trufflehog_scan_errors(stderr)

    assert_that(errors).is_equal_to([stderr])
    assert_that(
        scan_errors_are_all_benign(errors, scan_paths={"/repo/src/a.py"}),
    ).is_false()


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("", False),
        ("   \n\t", False),
        (
            '{"level":"info-0","msg":"running source"}\n'
            '{"level":"info-0","msg":"finished scanning","chunks":1}',
            False,
        ),
        (
            '{"level":"debug-2","msg":"chunk emitted"}\n'
            '{"level":"warn","msg":"detector deprecated"}',
            False,
        ),
        ("level=error msg=encountered errors during scan", True),
        (
            '{"level":"error","msg":"encountered errors during scan","errors":[]}',
            True,
        ),
        (
            '{"level":"info-0","msg":"running source"}\n'
            '{"level":"error","msg":"error scanning file",'
            '"error":"unable to open file: open /repo/x: permission denied"}\n'
            '{"level":"info-0","msg":"finished scanning"}',
            True,
        ),
        ('{"error":"read: broken pipe"}', True),
        ('{"unexpected":"structure","no":"level"}', True),
        ("scanning 12 files", True),
    ],
    ids=[
        "empty",
        "whitespace",
        "info-progress-only",
        "debug-and-warn-advisories-only",
        "plain-text-banner",
        "aggregate-json",
        "standalone-error-record-without-aggregate",
        "error-field-without-level",
        "unrecognised-json-structure",
        "unclassifiable-plain-text-trips-gate",
    ],
)
def test_stderr_reports_scan_errors(stderr: str, expected: bool) -> None:
    """The gate mirrors extraction exactly: retained lines trip it, noise does not.

    Args:
        stderr: Raw stderr captured from a TruffleHog run.
        expected: Whether the gate should engage error classification.
    """
    assert_that(stderr_reports_scan_errors(stderr)).is_equal_to(expected)


def test_plain_text_benign_missing_paths_remain_benign() -> None:
    """Plain-text stderr of only benign missing paths still passes."""
    stderr = (
        "lstat /ci/coverage: no such file or directory\n"
        "lstat /ci/lighthouse-reports: no such file or directory\n"
    )

    errors = extract_trufflehog_scan_errors(stderr)

    assert_that(
        scan_errors_are_all_benign(errors, scan_paths={"/repo/src/a.py"}),
    ).is_true()


def test_benign_missing_path_outside_scan_set() -> None:
    """Missing CI-only dirs outside the scan set are benign."""
    assert_that(
        is_benign_missing_path_error(
            "lstat /ci/coverage: no such file or directory",
            scan_paths={"/repo/src/a.py"},
        ),
    ).is_true()


def test_missing_path_in_scan_set_is_not_benign() -> None:
    """A missing path that was requested for scanning is not benign."""
    assert_that(
        is_benign_missing_path_error(
            "lstat /repo/src/a.py: no such file or directory",
            scan_paths={"/repo/src/a.py"},
        ),
    ).is_false()


def test_permission_denied_is_not_benign() -> None:
    """Permission errors are never classified as benign missing paths."""
    assert_that(
        is_benign_missing_path_error(
            "open /secret: permission denied",
            scan_paths={"/repo/src/a.py"},
        ),
    ).is_false()


def test_unresolvable_missing_path_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path.resolve failures must not classify the error as benign."""

    def _boom(self: Path) -> Path:
        raise OSError("cannot resolve")

    monkeypatch.setattr(Path, "resolve", _boom)

    assert_that(
        is_benign_missing_path_error(
            "lstat /ci/coverage: no such file or directory",
            scan_paths={"/repo/src/a.py"},
        ),
    ).is_false()


def test_scan_errors_all_benign_requires_non_empty() -> None:
    """Empty extracted errors must fail closed (not all-benign)."""
    assert_that(
        scan_errors_are_all_benign([], scan_paths={"/repo/a.py"}),
    ).is_false()


def test_scan_errors_all_benign_mixed_fails() -> None:
    """A mix of benign and genuine errors is not all-benign."""
    errors = [
        "lstat /ci/coverage: no such file or directory",
        "open /secret: permission denied",
    ]

    assert_that(
        scan_errors_are_all_benign(errors, scan_paths={"/repo/a.py"}),
    ).is_false()
