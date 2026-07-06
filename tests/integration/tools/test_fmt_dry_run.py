"""End-to-end integration tests for ``lintro fmt --dry-run``.

These tests require ruff to be installed and available in PATH. They exercise
the real tool-execution pipeline to verify that dry-run mode previews fixes
without modifying any files, and that exit codes follow check semantics.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.format import format_command

# Skip all tests if ruff is not installed.
pytestmark = pytest.mark.skipif(
    shutil.which("ruff") is None,
    reason="ruff not installed",
)

# Source with fixable issues: two unused imports and a missing-whitespace assign.
_FIXABLE_SOURCE = "import os\nimport sys\nx=1\n"
_CLEAN_SOURCE = "x = 1\n"
# Source whose only diagnostic (E741 ambiguous name) is NOT auto-fixable and is
# already correctly formatted, so a real fmt run would change nothing.
_NON_FIXABLE_SOURCE = "l = 0\n"


@pytest.fixture
def fixable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a Python file with fixable issues inside an isolated cwd.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture used to switch the working
            directory so run artifacts and config discovery stay isolated.

    Returns:
        Path to the created fixture file.
    """
    monkeypatch.chdir(tmp_path)
    file_path = tmp_path / "bad.py"
    file_path.write_text(_FIXABLE_SOURCE)
    return file_path


@pytest.fixture
def clean_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an already-formatted Python file inside an isolated cwd.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture used to switch the working
            directory so run artifacts and config discovery stay isolated.

    Returns:
        Path to the created fixture file.
    """
    monkeypatch.chdir(tmp_path)
    file_path = tmp_path / "clean.py"
    file_path.write_text(_CLEAN_SOURCE)
    return file_path


def test_dry_run_does_not_modify_files_and_lists_issues(fixable_file: Path) -> None:
    """Dry-run previews would-be fixes without writing and lists the issues.

    Args:
        fixable_file: Fixture file containing fixable issues.
    """
    runner = CliRunner()

    result = runner.invoke(
        format_command,
        ["--tools", "ruff", "--dry-run", str(fixable_file)],
    )

    # File content must be byte-for-byte unchanged.
    assert_that(fixable_file.read_text()).is_equal_to(_FIXABLE_SOURCE)

    # Preview must announce dry-run, list the fixable issues, and summarize.
    assert_that(result.output).contains("Dry run - no files modified")
    assert_that(result.output).contains("F401")
    assert_that(result.output).contains("Would fix")

    # Exit code 1 signals fixes are available (useful for CI checks).
    assert_that(result.exit_code).is_equal_to(1)


def test_dry_run_clean_file_reports_nothing(clean_file: Path) -> None:
    """Dry-run on a clean file reports nothing to fix and exits 0.

    Args:
        clean_file: Fixture file that is already formatted.
    """
    runner = CliRunner()

    result = runner.invoke(
        format_command,
        ["--tools", "ruff", "--dry-run", str(clean_file)],
    )

    assert_that(clean_file.read_text()).is_equal_to(_CLEAN_SOURCE)
    assert_that(result.output).contains("Dry run - no files modified")
    assert_that(result.exit_code).is_equal_to(0)


@pytest.fixture
def non_fixable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a file whose only issue is non-auto-fixable inside isolated cwd.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture used to switch the working
            directory so run artifacts and config discovery stay isolated.

    Returns:
        Path to the created fixture file.
    """
    monkeypatch.chdir(tmp_path)
    file_path = tmp_path / "ambiguous.py"
    file_path.write_text(_NON_FIXABLE_SOURCE)
    return file_path


def test_dry_run_only_non_fixable_issues_exits_zero(non_fixable_file: Path) -> None:
    """Dry-run reports no would-fix and exits 0 when issues are non-fixable.

    A file whose only diagnostic cannot be auto-fixed (and is already
    formatted) must not be reported as having fixes available.

    Args:
        non_fixable_file: Fixture file with a single non-fixable issue.
    """
    runner = CliRunner()

    result = runner.invoke(
        format_command,
        ["--tools", "ruff", "--dry-run", str(non_fixable_file)],
    )

    assert_that(non_fixable_file.read_text()).is_equal_to(_NON_FIXABLE_SOURCE)
    assert_that(result.output).contains("Dry run - no files modified")
    assert_that(result.output).does_not_contain("Would fix")
    assert_that(result.exit_code).is_equal_to(0)


def test_normal_fmt_still_writes_fixes(fixable_file: Path) -> None:
    """Normal fmt (no --dry-run) still writes fixes and does not regress.

    Args:
        fixable_file: Fixture file containing fixable issues.
    """
    runner = CliRunner()

    result = runner.invoke(
        format_command,
        ["--tools", "ruff", str(fixable_file)],
    )

    # The file must have been rewritten: unused imports removed and the
    # assignment reformatted.
    new_content = fixable_file.read_text()
    assert_that(new_content).is_not_equal_to(_FIXABLE_SOURCE)
    assert_that(new_content).does_not_contain("import os")
    assert_that(result.output).does_not_contain("Dry run - no files modified")
