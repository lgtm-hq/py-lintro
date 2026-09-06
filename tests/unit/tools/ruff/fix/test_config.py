"""Tests for execute_ruff_fix - Config file scenarios."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.tools.implementations.ruff.fix import execute_ruff_fix


def test_execute_ruff_fix_uses_config_args(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_empty_output: str,
) -> None:
    """Config args from ``_build_config_args`` reach every ruff invocation.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    mock_ruff_tool._build_config_args.return_value = ["--line-length", "100"]
    commands: list[list[str]] = []

    def fake_run(**kwargs: object) -> tuple[bool, str]:
        """Record the ruff argv and report a clean run.

        Args:
            **kwargs: Arguments ruff passed to ``_run_subprocess``.

        Returns:
            A successful run with empty JSON findings.
        """
        commands.append(list(cast("list[str]", kwargs["cmd"])))
        return (True, sample_ruff_json_empty_output)

    mock_ruff_tool._run_subprocess.side_effect = fake_run

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(commands).is_not_empty()
    for command in commands:
        assert_that(command).contains("--line-length", "100")
    assert_that(result.success).is_true()
