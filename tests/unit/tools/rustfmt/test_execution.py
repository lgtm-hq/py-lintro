"""Unit tests for rustfmt plugin execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.rustfmt import RustfmtPlugin

# =============================================================================
# Tests for RustfmtPlugin.check method
# =============================================================================


def test_check_no_cargo_toml(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Check skips gracefully when no Cargo.toml found.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test.rs"
    test_file.write_text("fn main() {}")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = rustfmt_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No Cargo.toml found")


def test_check_with_mocked_subprocess_success(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no issues found.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create Cargo.toml to enable rustfmt check
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text('[package]\nname = "test"\nversion = "0.1.0"')

    test_file = tmp_path / "src" / "main.rs"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("fn main() {}\n")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            rustfmt_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ),
    ):
        result = rustfmt_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_issues(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when formatting problems found.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text('[package]\nname = "test"\nversion = "0.1.0"')

    test_file = tmp_path / "src" / "main.rs"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("fn main(){let x=1;}")

    mock_output = (
        "Diff in src/main.rs:1:\n"
        "-fn main(){let x=1;}\n"
        "+fn main() {\n"
        "+    let x = 1;\n"
        "+}"
    )

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            rustfmt_plugin,
            "_run_subprocess",
            return_value=(False, mock_output),
        ),
    ):
        result = rustfmt_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_with_no_rust_files(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no Rust files found.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    non_rs_file = tmp_path / "test.txt"
    non_rs_file.write_text("Not a Rust file")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = rustfmt_plugin.check([str(non_rs_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No")


# =============================================================================
# Tests for RustfmtPlugin.fix method
# =============================================================================


def test_fix_no_cargo_toml(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix skips gracefully when no Cargo.toml found.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test.rs"
    test_file.write_text("fn main() {}")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = rustfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No Cargo.toml found")
    assert_that(result.fixed_issues_count).is_equal_to(0)


def test_fix_with_mocked_subprocess_success(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix returns success when fixes are applied.

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
        """Mock subprocess that returns diff on check, success on fix.

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
            # First check - issues found
            return (False, "Diff in src/main.rs:1:")
        elif call_count == 2:
            # Fix command
            return (True, "")
        else:
            # Verification - no issues
            return (True, "")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(rustfmt_plugin, "_run_subprocess", side_effect=mock_run),
    ):
        result = rustfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.fixed_issues_count).is_equal_to(1)
    # Verify the mock was called expected number of times (check + fix + verify)
    assert_that(call_count).is_equal_to(3)


def test_fix_with_nothing_to_fix(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix returns success when no fixes needed.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text('[package]\nname = "test"\nversion = "0.1.0"')

    test_file = tmp_path / "src" / "main.rs"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("fn main() {}\n")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            rustfmt_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ),
    ):
        result = rustfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.fixed_issues_count).is_equal_to(0)
