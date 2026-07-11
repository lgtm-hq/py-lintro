"""Unit tests for djLint plugin check and fix execution with mocked subprocess."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.djlint import DjlintPlugin

# A check diff indicating one file would be reformatted.
_CHECK_DIFF = (
    "\n\nbad.jinja\n"
    "──────────────────────────────\n"
    "@@ -1,2 +1,2 @@\n"
    '-<img src="a.png">\n'
    '+    <img src="a.png">\n\n'
    "1 file would be updated.\n"
)

# Clean check output (nothing to reformat).
_CHECK_CLEAN = "Checking 1/1 files\n\n0 files would be updated.\n"

# Reformat output (djLint exits non-zero when it rewrites a file).
_REFORMAT_OUTPUT = (
    "\n\nbad.jinja\n"
    "──────────────────────────────\n"
    "@@ -1,2 +1,2 @@\n"
    '-<img src="a.png">\n'
    '+    <img src="a.png">\n\n'
    "1 file was updated.\n"
)


def test_check_success_when_clean(
    djlint_plugin: DjlintPlugin,
    tmp_path: Path,
) -> None:
    """Check reports success when djLint finds nothing to reformat.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
        tmp_path: Temporary directory for test files.
    """
    test_file = tmp_path / "page.jinja"
    test_file.write_text("<div>\n    <p>ok</p>\n</div>\n")

    with patch.object(
        djlint_plugin,
        "_run_subprocess",
        return_value=(True, _CHECK_CLEAN),
    ):
        result = djlint_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_reports_issues(
    djlint_plugin: DjlintPlugin,
    tmp_path: Path,
) -> None:
    """Check reports issues when djLint would reformat a file.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
        tmp_path: Temporary directory for test files.
    """
    test_file = tmp_path / "bad.jinja"
    test_file.write_text('<div>\n<img src="a.png">\n</div>\n')

    with patch.object(
        djlint_plugin,
        "_run_subprocess",
        return_value=(False, _CHECK_DIFF),
    ):
        result = djlint_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_no_matching_files(
    djlint_plugin: DjlintPlugin,
    tmp_path: Path,
) -> None:
    """Check succeeds and reports no work when no template files match.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
        tmp_path: Temporary directory for test files.
    """
    other_file = tmp_path / "notes.txt"
    other_file.write_text("not a template")

    result = djlint_plugin.check([str(other_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No")


def test_fix_reformats_and_reports_metrics(
    djlint_plugin: DjlintPlugin,
    tmp_path: Path,
) -> None:
    """Fix reformats a file and reports accurate auto-fix metrics.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
        tmp_path: Temporary directory for test files.
    """
    test_file = tmp_path / "bad.jinja"
    test_file.write_text('<div>\n<img src="a.png">\n</div>\n')

    with patch.object(
        djlint_plugin,
        "_run_subprocess",
        side_effect=[
            (False, _CHECK_DIFF),  # initial check - issue found
            (False, _REFORMAT_OUTPUT),  # reformat (non-zero on change)
            (True, _CHECK_CLEAN),  # verify - clean
        ],
    ):
        result = djlint_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.initial_issues).is_length(1)


def test_fix_no_changes_when_clean(
    djlint_plugin: DjlintPlugin,
    tmp_path: Path,
) -> None:
    """Fix skips reformat when the initial check is already clean.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
        tmp_path: Temporary directory for test files.
    """
    test_file = tmp_path / "page.jinja"
    test_file.write_text("<div>\n    <p>ok</p>\n</div>\n")

    with patch.object(
        djlint_plugin,
        "_run_subprocess",
        return_value=(True, _CHECK_CLEAN),
    ) as mock_run:
        result = djlint_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.fixed_issues_count).is_equal_to(0)
    # Only the initial check runs; reformat and verify are skipped.
    assert_that(mock_run.call_count).is_equal_to(1)


def test_fix_reports_remaining_when_verify_dirty(
    djlint_plugin: DjlintPlugin,
    tmp_path: Path,
) -> None:
    """Fix surfaces remaining issues when verification still shows a diff.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
        tmp_path: Temporary directory for test files.
    """
    test_file = tmp_path / "bad.jinja"
    test_file.write_text('<div>\n<img src="a.png">\n</div>\n')

    with patch.object(
        djlint_plugin,
        "_run_subprocess",
        side_effect=[
            (False, _CHECK_DIFF),  # initial check - issue found
            (False, _REFORMAT_OUTPUT),  # reformat
            (False, _CHECK_DIFF),  # verify - still dirty
        ],
    ):
        result = djlint_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(0)


def test_fix_no_matching_files(
    djlint_plugin: DjlintPlugin,
    tmp_path: Path,
) -> None:
    """Fix succeeds and reports no work when no template files match.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
        tmp_path: Temporary directory for test files.
    """
    other_file = tmp_path / "notes.txt"
    other_file.write_text("not a template")

    result = djlint_plugin.fix([str(other_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No")
