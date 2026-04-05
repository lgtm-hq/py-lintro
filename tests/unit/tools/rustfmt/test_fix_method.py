"""Tests for RustfmtPlugin.fix method initial_issues population."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.rustfmt import RustfmtPlugin


def test_fix_populates_initial_issues(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix populates initial_issues when issues are found and fixed.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text('[package]\nname = "test"\nversion = "0.1.0"')

    test_file = tmp_path / "src" / "main.rs"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("fn main(){}")

    call_count = 0

    def mock_run(
        cmd: list[str],
        timeout: int,
        cwd: str | None = None,
    ) -> tuple[bool, str]:
        """Mock subprocess for check→fix→verify.

        Args:
            cmd: Command list.
            timeout: Timeout in seconds.
            cwd: Working directory.

        Returns:
            Tuple of (success, output).
        """
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (False, "Diff in src/main.rs:1:")
        elif call_count == 2:
            return (True, "")
        else:
            return (True, "")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(rustfmt_plugin, "_run_subprocess", side_effect=mock_run):
            result = rustfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues).is_not_none()
    assert_that(result.initial_issues).is_length(1)
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)


def test_fix_initial_issues_none_when_no_issues(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix sets initial_issues to None when no issues detected.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text('[package]\nname = "test"\nversion = "0.1.0"')

    test_file = tmp_path / "src" / "main.rs"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("fn main() {}\n")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            rustfmt_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ):
            result = rustfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues).is_none()


def test_fix_partial_fix_preserves_initial_issues(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix preserves initial_issues when some issues remain after fix.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text('[package]\nname = "test"\nversion = "0.1.0"')

    test_file = tmp_path / "src" / "main.rs"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("fn main(){}")

    call_count = 0

    def mock_run(
        cmd: list[str],
        timeout: int,
        cwd: str | None = None,
    ) -> tuple[bool, str]:
        """Mock subprocess where fix doesn't resolve all issues.

        Args:
            cmd: Command list.
            timeout: Timeout in seconds.
            cwd: Working directory.

        Returns:
            Tuple of (success, output).
        """
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Initial check: 2 issues (different files for dedup)
            return (False, "Diff in src/main.rs:1:\nDiff in src/lib.rs:1:")
        elif call_count == 2:
            return (True, "")
        else:
            # Verify: 1 issue remains
            return (False, "Diff in src/lib.rs:1:")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(rustfmt_plugin, "_run_subprocess", side_effect=mock_run):
            result = rustfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues).is_not_none()
    assert_that(result.initial_issues).is_length(2)
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(1)
