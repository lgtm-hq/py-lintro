"""Unit tests for rustfmt plugin error handling."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.parsers.rustfmt.rustfmt_parser import parse_rustfmt_output
from lintro.tools.definitions.rustfmt import RustfmtPlugin

# =============================================================================
# Tests for timeout handling
# =============================================================================


def test_check_with_timeout(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout correctly.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text('[package]\nname = "test"\nversion = "0.1.0"')

    test_file = tmp_path / "src" / "main.rs"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("fn main() {}")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            rustfmt_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=["cargo", "fmt"], timeout=60),
        ),
    ):
        result = rustfmt_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")
    assert_that(result.issues_count).is_equal_to(1)


def test_fix_with_timeout_on_initial_check(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix handles timeout on initial check correctly.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text('[package]\nname = "test"\nversion = "0.1.0"')

    test_file = tmp_path / "src" / "main.rs"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("fn main() {}")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            rustfmt_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=["cargo", "fmt"], timeout=60),
        ),
    ):
        result = rustfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")
    # Timeout is counted as an execution failure (consistent with clippy)
    assert_that(result.issues_count).is_equal_to(1)


def test_fix_with_timeout_on_fix_command(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix handles timeout on fix command correctly.

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
        """Mock subprocess that times out on fix command.

        Args:
            cmd: Command list.
            timeout: Timeout in seconds.
            cwd: Working directory.

        Returns:
            tuple[bool, str]: Tuple of (success, output).

        Raises:
            subprocess.TimeoutExpired: On second call (fix command).
        """
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First check - issues found
            return (False, "Diff in src/main.rs:1:")
        # Fix command times out
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(rustfmt_plugin, "_run_subprocess", side_effect=mock_run),
    ):
        result = rustfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(0)


def test_fix_with_timeout_on_verification(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix handles timeout on verification check correctly.

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
        """Mock subprocess that times out on verification.

        Args:
            cmd: Command list.
            timeout: Timeout in seconds.
            cwd: Working directory.

        Returns:
            tuple[bool, str]: Tuple of (success, output).

        Raises:
            subprocess.TimeoutExpired: On third call (verification).
        """
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First check - issues found
            return (False, "Diff in src/main.rs:1:")
        elif call_count == 2:
            # Fix command succeeds
            return (True, "")
        # Verification times out
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(rustfmt_plugin, "_run_subprocess", side_effect=mock_run),
    ):
        result = rustfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(0)


# =============================================================================
# Tests for output parsing
# =============================================================================


def test_parse_rustfmt_output_with_diff() -> None:
    """Parse diff output from rustfmt."""
    output = """Diff in src/main.rs:1:
-fn main(){let x=1;}
+fn main() {
+    let x = 1;
+}"""
    issues = parse_rustfmt_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).contains("main.rs")


def test_parse_rustfmt_output_multiple_files() -> None:
    """Parse output with multiple file diffs."""
    output = """Diff in src/main.rs:1:
-fn main(){}
+fn main() {}
Diff in src/lib.rs:5:
-fn foo(){}
+fn foo() {}"""
    issues = parse_rustfmt_output(output)

    assert_that(issues).is_length(2)


def test_parse_rustfmt_output_empty() -> None:
    """Parse empty output returns empty list."""
    issues = parse_rustfmt_output("")

    assert_that(issues).is_empty()


def test_parse_rustfmt_output_none() -> None:
    """Parse None output returns empty list."""
    issues = parse_rustfmt_output(None)

    assert_that(issues).is_empty()
