"""Unit tests for TruffleHog scan-error classification helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
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
