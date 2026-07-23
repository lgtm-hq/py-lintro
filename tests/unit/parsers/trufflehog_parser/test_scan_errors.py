"""Unit tests for TruffleHog scan-error classification helpers."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.trufflehog.trufflehog_errors import (
    extract_trufflehog_scan_errors,
    is_benign_missing_path_error,
    scan_errors_are_all_benign,
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


def test_extract_errors_empty_when_banner_has_no_details() -> None:
    """A bare scan-error banner without reasons yields an empty list."""
    stderr = "level=error msg=encountered errors during scan"

    assert_that(extract_trufflehog_scan_errors(stderr)).is_empty()


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
