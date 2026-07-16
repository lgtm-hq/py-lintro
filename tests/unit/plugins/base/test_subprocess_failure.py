"""Unit tests for subprocess failure diagnostics."""

from __future__ import annotations

from assertpy import assert_that

from lintro.plugins.subprocess_failure import format_subprocess_failure


def test_format_subprocess_failure_includes_exit_code_and_command() -> None:
    """Empty-output failures should include exit code and command preview."""
    message = format_subprocess_failure(
        tool_name="mypy",
        cmd=["mypy", "--output", "json", "."],
        returncode=2,
        cwd="/code",
        timeout=60,
    )

    assert_that(message).contains("mypy execution failed (exit code 2)")
    assert_that(message).contains("Command: mypy --output json .")
    assert_that(message).contains("Working directory: /code")
    assert_that(message).contains("Timeout: 60s")


def test_format_subprocess_failure_oom_hint_for_137() -> None:
    """Exit code 137 should mention likely OOM kill."""
    message = format_subprocess_failure(
        tool_name="mypy",
        cmd=["mypy", "."],
        returncode=137,
    )

    assert_that(message).contains("exit code 137")
    assert_that(message).contains("likely killed")


def test_format_subprocess_failure_preserves_existing_output() -> None:
    """When stderr exists, return it unchanged."""
    message = format_subprocess_failure(
        tool_name="mypy",
        cmd=["mypy", "."],
        returncode=1,
        stderr="error: cannot find module",
    )

    assert_that(message).is_equal_to("error: cannot find module")
