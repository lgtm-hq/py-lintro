"""Tests for config file detection and usage in execute_ruff_check."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.tools.ruff.check import execute_ruff_check


def test_execute_ruff_check_uses_cwd_for_config_discovery(
    mock_ruff_tool: MagicMock,
) -> None:
    """Run ruff from the cwd the preparation pipeline resolved.

    Ruff discovers its configuration relative to the working directory, so the
    context's cwd has to reach the subprocess call for a project-local
    ``pyproject.toml`` to be picked up.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    working_dirs: list[str | None] = []

    def fake_run(**kwargs: object) -> tuple[bool, str]:
        """Record ruff's working directory and report a clean run.

        Args:
            **kwargs: Arguments the caller passed to the runner.

        Returns:
            A successful run with empty JSON findings.
        """
        working_dirs.append(cast("str | None", kwargs.get("cwd")))
        return (True, "[]")

    with (
        patch(
            "lintro.tools.ruff.check.run_subprocess_with_timeout",
            side_effect=fake_run,
        ),
        patch(
            "lintro.tools.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

    assert_that(working_dirs).is_equal_to(["/test/project"])
    assert_that(result.success).is_true()


def test_execute_ruff_check_with_config_args(
    mock_ruff_tool: MagicMock,
) -> None:
    """Config args reach the ruff argv that is actually executed.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    mock_ruff_tool._build_config_args.return_value = [
        "--line-length",
        "100",
    ]
    commands: list[list[str]] = []

    def fake_run(**kwargs: object) -> tuple[bool, str]:
        """Record the ruff argv and report a clean run.

        Args:
            **kwargs: Arguments ruff passed to the timeout-aware runner.

        Returns:
            A successful run with empty JSON findings.
        """
        commands.append(list(cast("list[str]", kwargs["cmd"])))
        return (True, "[]")

    with (
        patch(
            "lintro.tools.ruff.check.run_subprocess_with_timeout",
            side_effect=fake_run,
        ),
        patch(
            "lintro.tools.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

    assert_that(commands).is_length(1)
    assert_that(commands[0]).contains("--line-length", "100")
    assert_that(result.success).is_true()
