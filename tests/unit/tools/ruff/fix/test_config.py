"""Tests for execute_ruff_fix - Config file scenarios."""

from __future__ import annotations

from unittest.mock import MagicMock

from lintro.tools.implementations.ruff.fix import execute_ruff_fix


def test_execute_ruff_fix_uses_config_args(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_empty_output: str,
) -> None:
    """Use config args from _build_config_args when available.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    mock_ruff_tool._build_config_args.return_value = ["--line-length", "100"]

    mock_ruff_tool._run_subprocess.side_effect = [
        (True, sample_ruff_json_empty_output),
        (True, sample_ruff_json_empty_output),
    ]

    execute_ruff_fix(mock_ruff_tool, ["test.py"])

    # Verify _build_config_args was called (via build_ruff_check_command)
    mock_ruff_tool._build_config_args.assert_called()
