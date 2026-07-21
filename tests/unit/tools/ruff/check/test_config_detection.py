"""Tests for config file detection and usage in execute_ruff_check."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.tools.implementations.ruff.check import execute_ruff_check


def test_execute_ruff_check_uses_cwd_for_config_discovery(
    mock_ruff_tool: MagicMock,
) -> None:
    """Use cwd from the prepared execution context for config discovery.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(True, "[]"),
        ) as mock_subprocess,
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        execute_ruff_check(mock_ruff_tool, ["/test/project"])

        # Verify the shared preparation pipeline resolved the working directory
        mock_ruff_tool._prepare_execution.assert_called()

        # Verify subprocess was called with cwd from the execution context
        mock_subprocess.assert_called()
        call_kwargs = mock_subprocess.call_args
        assert_that(call_kwargs.kwargs.get("cwd")).is_equal_to("/test/project")


def test_execute_ruff_check_with_config_args(
    mock_ruff_tool: MagicMock,
) -> None:
    """Include config args in command when provided.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    mock_ruff_tool._build_config_args.return_value = [
        "--line-length",
        "100",
    ]

    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(True, "[]"),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
        patch(
            "lintro.tools.implementations.ruff.commands.build_ruff_check_command",
        ) as mock_build_cmd,
    ):
        mock_build_cmd.return_value = [
            "ruff",
            "check",
            "--line-length",
            "100",
            "test.py",
        ]

        execute_ruff_check(mock_ruff_tool, ["/test/project"])

        mock_build_cmd.assert_called_once()
