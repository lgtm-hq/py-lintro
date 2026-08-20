"""Unit tests for clippy plugin check execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.parsers.clippy.clippy_issue import ClippyIssue
from lintro.tools.definitions.clippy import ClippyPlugin

_CLIPPY_ISSUE = (
    '{"reason":"compiler-message","message":{"code":{"code":"clippy::needless_return"},'
    '"level":"warning","message":"unneeded `return` statement",'
    '"spans":[{"file_name":"src/lib.rs","line_start":42,"line_end":42,'
    '"column_start":5,"column_end":15}],'
    '"rendered":"warning: unneeded `return` statement..."}}'
)


def _cargo_project(tmp_path: Path) -> Path:
    """Create a minimal Cargo project under ``tmp_path``.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to a ``main.rs`` file in that project.
    """
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n')
    rs_file = tmp_path / "main.rs"
    rs_file.write_text("fn main() {}\n")
    return rs_file


# Tests for ClippyPlugin.check method


def test_check_without_cargo_toml_skips(
    clippy_plugin: ClippyPlugin,
    tmp_path: Path,
) -> None:
    """Check skips cleanly when no Cargo.toml is present.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    rs_file = tmp_path / "main.rs"
    rs_file.write_text("fn main() {}\n")

    result = clippy_plugin.check([str(rs_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No Cargo.toml found")


def test_check_with_clean_run(clippy_plugin: ClippyPlugin, tmp_path: Path) -> None:
    """Check reports success when clippy finds no issues.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    rs_file = _cargo_project(tmp_path)

    with patch.object(clippy_plugin, "_run_subprocess", return_value=(True, "")):
        result = clippy_plugin.check([str(rs_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_reports_parsed_issues(
    clippy_plugin: ClippyPlugin,
    tmp_path: Path,
) -> None:
    """Check surfaces Clippy JSON findings as issues, not a clean skip.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    rs_file = _cargo_project(tmp_path)

    with patch.object(
        clippy_plugin,
        "_run_subprocess",
        return_value=(False, _CLIPPY_ISSUE),
    ):
        result = clippy_plugin.check([str(rs_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    issues = result.issues
    assert issues is not None  # narrow type for mypy
    first_issue = issues[0]
    assert isinstance(first_issue, ClippyIssue)  # narrow type for mypy
    assert_that(first_issue.code).is_equal_to("clippy::needless_return")
    assert_that(first_issue.file).is_equal_to("src/lib.rs")


def test_fix_counts_initial_and_remaining(
    clippy_plugin: ClippyPlugin,
    tmp_path: Path,
) -> None:
    """Fix records the initial finding and a clean post-fix re-check.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    rs_file = _cargo_project(tmp_path)

    with patch.object(
        clippy_plugin,
        "_run_subprocess",
        side_effect=[
            (False, _CLIPPY_ISSUE),
            (True, ""),
            (True, ""),
        ],
    ):
        result = clippy_plugin.fix([str(rs_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)
