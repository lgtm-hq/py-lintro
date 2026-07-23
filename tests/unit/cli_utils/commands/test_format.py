"""Tests for lintro.cli_utils.commands.format module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.format import (
    DEFAULT_ACTION,
    DEFAULT_EXIT_CODE,
    DEFAULT_PATHS,
    format_code,
    format_command,
)

# =============================================================================
# Module constants tests
# =============================================================================


def test_default_paths_is_current_dir() -> None:
    """DEFAULT_PATHS is current directory."""
    assert_that(DEFAULT_PATHS).is_equal_to(["."])


def test_default_exit_code_is_zero() -> None:
    """DEFAULT_EXIT_CODE is zero."""
    assert_that(DEFAULT_EXIT_CODE).is_equal_to(0)


def test_default_action_is_fmt() -> None:
    """DEFAULT_ACTION is 'fmt'."""
    assert_that(DEFAULT_ACTION).is_equal_to("fmt")


# =============================================================================
# format_command tests
# =============================================================================


@patch("lintro.cli_utils.commands.format.run_lint_tools_simple")
def test_format_command_defaults_to_current_dir(mock_run: MagicMock) -> None:
    """format_command uses current directory when no paths given.

    Args:
        mock_run: Mock for run_lint_tools_simple.
    """
    mock_run.return_value = 0
    runner = CliRunner()

    result = runner.invoke(format_command, [])

    assert_that(result.exit_code).is_equal_to(0)
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args.kwargs
    assert_that(call_kwargs["paths"]).is_equal_to(["."])
    assert_that(call_kwargs["action"]).is_equal_to("fmt")


@patch("lintro.cli_utils.commands.format.run_lint_tools_simple")
def test_format_command_with_paths(mock_run: MagicMock, tmp_path: Path) -> None:
    """format_command passes provided paths.

    Args:
        mock_run: Mock for run_lint_tools_simple.
        tmp_path: Temporary path fixture.
    """
    mock_run.return_value = 0
    runner = CliRunner()

    result = runner.invoke(format_command, [str(tmp_path)])

    assert_that(result.exit_code).is_equal_to(0)
    call_kwargs = mock_run.call_args.kwargs
    assert_that(call_kwargs["paths"]).contains(str(tmp_path))


@patch("lintro.cli_utils.commands.format.run_lint_tools_simple")
def test_format_command_with_tools_option(mock_run: MagicMock) -> None:
    """format_command passes tools option.

    Args:
        mock_run: Mock for run_lint_tools_simple.
    """
    mock_run.return_value = 0
    runner = CliRunner()

    result = runner.invoke(format_command, ["--tools", "ruff,black"])

    assert_that(result.exit_code).is_equal_to(0)
    call_kwargs = mock_run.call_args.kwargs
    assert_that(call_kwargs["tools"]).is_equal_to("ruff,black")


@patch("lintro.cli_utils.commands.format.run_lint_tools_simple")
def test_format_command_with_exclude(mock_run: MagicMock) -> None:
    """format_command passes exclude option.

    Args:
        mock_run: Mock for run_lint_tools_simple.
    """
    mock_run.return_value = 0
    runner = CliRunner()

    result = runner.invoke(format_command, ["--exclude", "*.pyc"])

    assert_that(result.exit_code).is_equal_to(0)
    call_kwargs = mock_run.call_args.kwargs
    assert_that(call_kwargs["exclude"]).is_equal_to("*.pyc")


@patch("lintro.cli_utils.commands.format.run_lint_tools_simple")
def test_format_command_with_include_venv(mock_run: MagicMock) -> None:
    """format_command passes include_venv flag.

    Args:
        mock_run: Mock for run_lint_tools_simple.
    """
    mock_run.return_value = 0
    runner = CliRunner()

    result = runner.invoke(format_command, ["--include-venv"])

    assert_that(result.exit_code).is_equal_to(0)
    call_kwargs = mock_run.call_args.kwargs
    assert_that(call_kwargs["include_venv"]).is_true()


@patch("lintro.cli_utils.commands.format.run_lint_tools_simple")
def test_format_command_with_verbose(mock_run: MagicMock) -> None:
    """format_command passes verbose flag.

    Args:
        mock_run: Mock for run_lint_tools_simple.
    """
    mock_run.return_value = 0
    runner = CliRunner()

    result = runner.invoke(format_command, ["-v"])

    assert_that(result.exit_code).is_equal_to(0)
    call_kwargs = mock_run.call_args.kwargs
    assert_that(call_kwargs["verbose"]).is_true()


@patch("lintro.cli_utils.commands.format.run_lint_tools_simple")
def test_format_command_with_output_format(mock_run: MagicMock) -> None:
    """format_command passes output_format option.

    Args:
        mock_run: Mock for run_lint_tools_simple.
    """
    mock_run.return_value = 0
    runner = CliRunner()

    result = runner.invoke(format_command, ["--output-format", "json"])

    assert_that(result.exit_code).is_equal_to(0)
    call_kwargs = mock_run.call_args.kwargs
    assert_that(call_kwargs["output_format"]).is_equal_to("json")


@patch("lintro.cli_utils.commands.format.run_lint_tools_simple")
def test_format_command_dry_run_flag_passed(mock_run: MagicMock) -> None:
    """format_command forwards --dry-run as dry_run=True.

    Args:
        mock_run: Mock for run_lint_tools_simple.
    """
    mock_run.return_value = 0
    runner = CliRunner()

    result = runner.invoke(format_command, ["--dry-run"])

    assert_that(result.exit_code).is_equal_to(0)
    call_kwargs = mock_run.call_args.kwargs
    assert_that(call_kwargs["dry_run"]).is_true()


@patch("lintro.cli_utils.commands.format.run_lint_tools_simple")
def test_format_command_dry_run_defaults_false(mock_run: MagicMock) -> None:
    """format_command defaults dry_run to False when flag absent.

    Args:
        mock_run: Mock for run_lint_tools_simple.
    """
    mock_run.return_value = 0
    runner = CliRunner()

    result = runner.invoke(format_command, [])

    assert_that(result.exit_code).is_equal_to(0)
    call_kwargs = mock_run.call_args.kwargs
    assert_that(call_kwargs["dry_run"]).is_false()


@patch("lintro.cli_utils.commands.format.run_lint_tools_simple")
def test_format_command_returns_tool_exit_code(mock_run: MagicMock) -> None:
    """format_command returns exit code from tool execution.

    Args:
        mock_run: Mock for run_lint_tools_simple.
    """
    mock_run.return_value = 1
    runner = CliRunner()

    result = runner.invoke(format_command, [])

    assert_that(result.exit_code).is_equal_to(1)


# =============================================================================
# format_code tests
# =============================================================================


@patch("lintro.api.core.run_lint_tools_simple")
def test_format_code_invokes_command(mock_run: MagicMock) -> None:
    """format_code routes through the library API.

    Args:
        mock_run: Mock for run_lint_tools_simple used by the API.
    """
    mock_run.return_value = 0

    format_code(paths=["src/"])

    mock_run.assert_called_once()


@patch("lintro.api.core.run_lint_tools_simple")
def test_format_code_raises_on_failure(mock_run: MagicMock) -> None:
    """format_code raises RuntimeError on non-zero exit.

    Args:
        mock_run: Mock for run_lint_tools_simple used by the API.
    """
    mock_run.return_value = 1

    with pytest.raises(RuntimeError, match="Format failed"):
        format_code(paths=["src/"])


@patch("lintro.api.core.run_lint_tools_simple")
def test_format_code_passes_options(mock_run: MagicMock) -> None:
    """format_code passes all options through to the API.

    Args:
        mock_run: Mock for run_lint_tools_simple used by the API.
    """
    mock_run.return_value = 0

    format_code(
        paths=["src/"],
        tools="ruff",
        exclude="*.bak",
        include_venv=True,
        verbose=True,
    )

    call_kwargs = mock_run.call_args.kwargs
    assert_that(call_kwargs["paths"]).is_equal_to(["src/"])
    assert_that(call_kwargs["tools"]).is_equal_to("ruff")
    assert_that(call_kwargs["exclude"]).is_equal_to("*.bak")
    assert_that(call_kwargs["include_venv"]).is_true()
    assert_that(call_kwargs["verbose"]).is_true()
