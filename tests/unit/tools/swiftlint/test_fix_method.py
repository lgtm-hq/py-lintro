"""Unit tests for SwiftlintPlugin.fix and its issue-count invariant."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.swiftlint import SwiftlintPlugin
from tests.unit.tools.swiftlint.conftest import FIXABLE_JSON, SAMPLE_JSON


def _sp(success: bool, output: str) -> SubprocessResult:
    """Build a SubprocessResult from a legacy (success, output) pair.

    Args:
        success: Whether the simulated process exited 0.
        output: Simulated stdout (also used as combined output).

    Returns:
        SubprocessResult with the JSON payload on stdout.
    """
    return SubprocessResult(
        returncode=0 if success else 1,
        stdout=output,
        stderr="",
        output=output,
    )


def test_fix_corrects_fixable_issue(
    swiftlint_plugin: SwiftlintPlugin,
    tmp_path: Path,
) -> None:
    """Fix resolves an auto-correctable issue; invariant holds (1 = 1 + 0).

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
        tmp_path: Temporary directory for test files.
    """
    test_file = tmp_path / "Sample.swift"
    test_file.write_text("let x = 1 ;\n")

    calls: list[list[str]] = []

    def mock_run(cmd: list[str], timeout: int) -> SubprocessResult:
        """Return check JSON, fix success, then a clean re-check.

        Args:
            cmd: The subprocess command.
            timeout: Timeout in seconds.

        Returns:
            Tuple of (success, output).
        """
        calls.append(cmd)
        if "--fix" in cmd:
            return _sp(True, "")
        # First lint call reports the issue; the re-check reports none.
        lint_calls = [c for c in calls if "lint" in c]
        if len(lint_calls) == 1:
            return _sp(False, FIXABLE_JSON)
        return _sp(True, "[\n\n]")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            swiftlint_plugin,
            "_run_subprocess_result",
            side_effect=mock_run,
        ):
            result = swiftlint_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    # Invariant: initial == fixed + remaining.
    assert_that(result.initial_issues_count).is_equal_to(
        (result.fixed_issues_count or 0) + (result.remaining_issues_count or 0),
    )


def test_fix_leaves_unfixable_issue(
    swiftlint_plugin: SwiftlintPlugin,
    tmp_path: Path,
) -> None:
    """Non-correctable issues remain; invariant holds (2 = 0 + 2).

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
        tmp_path: Temporary directory for test files.
    """
    test_file = tmp_path / "Sample.swift"
    test_file.write_text("class foo {}\n")

    def mock_run(cmd: list[str], timeout: int) -> SubprocessResult:
        """Return the same issues before and after the fix attempt.

        Args:
            cmd: The subprocess command.
            timeout: Timeout in seconds.

        Returns:
            Tuple of (success, output).
        """
        if "--fix" in cmd:
            return _sp(True, "")
        return _sp(False, SAMPLE_JSON)

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            swiftlint_plugin,
            "_run_subprocess_result",
            side_effect=mock_run,
        ):
            result = swiftlint_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.remaining_issues_count).is_equal_to(2)
    assert_that(result.initial_issues).is_not_none()
    assert_that(result.initial_issues_count).is_equal_to(
        (result.fixed_issues_count or 0) + (result.remaining_issues_count or 0),
    )


def test_fix_nothing_to_fix(
    swiftlint_plugin: SwiftlintPlugin,
    tmp_path: Path,
) -> None:
    """Fix reports no work and does not run the correct step on a clean file.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
        tmp_path: Temporary directory for test files.
    """
    test_file = tmp_path / "Clean.swift"
    test_file.write_text("let greeting = 1\n")

    fix_invoked = False

    def mock_run(cmd: list[str], timeout: int) -> SubprocessResult:
        """Track whether the --fix subprocess is invoked.

        Args:
            cmd: The subprocess command.
            timeout: Timeout in seconds.

        Returns:
            Tuple of (success, output).
        """
        nonlocal fix_invoked
        if "--fix" in cmd:
            fix_invoked = True
            return _sp(True, "")
        return _sp(True, "[\n\n]")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            swiftlint_plugin,
            "_run_subprocess_result",
            side_effect=mock_run,
        ):
            result = swiftlint_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues).is_none()
    assert_that(result.output).contains("No fixes needed")
    assert_that(fix_invoked).is_false()
