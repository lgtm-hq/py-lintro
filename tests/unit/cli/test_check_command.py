"""Unit tests for lintro/cli_utils/commands/check.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.check import check, check_command

# =============================================================================
# Check Command Basic Tests
# =============================================================================


def test_check_command_help(cli_runner: CliRunner) -> None:
    """Verify check command shows help.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(check_command, ["--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Check files")


def test_check_command_default_paths(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
) -> None:
    """Verify check command uses default paths when none provided.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(check_command, [])

    mock_run_lint_tools_check.assert_called_once()
    call_kwargs = mock_run_lint_tools_check.call_args.kwargs
    assert_that(call_kwargs["paths"]).is_equal_to(["."])


def test_check_command_with_paths(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
    tmp_path: Path,
) -> None:
    """Verify check command passes provided paths.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
        tmp_path: Temporary directory path for testing.
    """
    test_file = tmp_path / "test.py"
    test_file.write_text("# test")

    cli_runner.invoke(check_command, [str(test_file)])

    mock_run_lint_tools_check.assert_called_once()
    call_kwargs = mock_run_lint_tools_check.call_args.kwargs
    assert_that(call_kwargs["paths"]).contains(str(test_file))


def test_check_command_exit_code_zero_on_success(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
) -> None:
    """Verify check command exits with 0 on success.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
    """
    mock_run_lint_tools_check.return_value = 0
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(check_command, [])

    assert_that(result.exit_code).is_equal_to(0)


def test_check_command_exit_code_nonzero_on_issues(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
) -> None:
    """Verify check command exits with non-zero when issues found.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
    """
    mock_run_lint_tools_check.return_value = 1
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(check_command, [])

    assert_that(result.exit_code).is_equal_to(1)


# =============================================================================
# Check Command Options Tests
# =============================================================================


def test_check_command_tools_option(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
) -> None:
    """Verify --tools option is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(check_command, ["--tools", "ruff,mypy"])

    mock_run_lint_tools_check.assert_called_once()
    call_kwargs = mock_run_lint_tools_check.call_args.kwargs
    assert_that(call_kwargs["tools"]).is_equal_to("ruff,mypy")


def test_check_command_exclude_option(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
) -> None:
    """Verify --exclude option is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(check_command, ["--exclude", "*.pyc,__pycache__"])

    mock_run_lint_tools_check.assert_called_once()
    call_kwargs = mock_run_lint_tools_check.call_args.kwargs
    assert_that(call_kwargs["exclude"]).is_equal_to("*.pyc,__pycache__")


def test_check_command_include_venv_flag(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
) -> None:
    """Verify --include-venv flag is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(check_command, ["--include-venv"])

    mock_run_lint_tools_check.assert_called_once()
    call_kwargs = mock_run_lint_tools_check.call_args.kwargs
    assert_that(call_kwargs["include_venv"]).is_true()


def test_check_command_output_format_option(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
) -> None:
    """Verify --output-format option is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(check_command, ["--output-format", "json"])

    mock_run_lint_tools_check.assert_called_once()
    call_kwargs = mock_run_lint_tools_check.call_args.kwargs
    assert_that(call_kwargs["output_format"]).is_equal_to("json")


@pytest.mark.parametrize(
    "format_option",
    ["plain", "grid", "markdown", "html", "json", "csv"],
    ids=["plain", "grid", "markdown", "html", "json", "csv"],
)
def test_check_command_output_format_valid_choices(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
    format_option: str,
) -> None:
    """Verify all valid output format choices are accepted.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
        format_option: The output format option value to test.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(check_command, ["--output-format", format_option])

    assert_that(result.exit_code).is_equal_to(0)


def test_check_command_group_by_option(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
) -> None:
    """Verify --group-by option is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(check_command, ["--group-by", "code"])

    mock_run_lint_tools_check.assert_called_once()
    call_kwargs = mock_run_lint_tools_check.call_args.kwargs
    assert_that(call_kwargs["group_by"]).is_equal_to("code")


@pytest.mark.parametrize(
    "group_by_option",
    ["file", "code", "none", "auto", "category"],
    ids=["file", "code", "none", "auto", "category"],
)
def test_check_command_group_by_valid_choices(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
    group_by_option: str,
) -> None:
    """Verify all valid group-by choices are accepted.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
        group_by_option: The group-by option value to test.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(check_command, ["--group-by", group_by_option])

    assert_that(result.exit_code).is_equal_to(0)


def test_check_command_verbose_flag(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
) -> None:
    """Verify --verbose flag is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(check_command, ["--verbose"])

    mock_run_lint_tools_check.assert_called_once()
    call_kwargs = mock_run_lint_tools_check.call_args.kwargs
    assert_that(call_kwargs["verbose"]).is_true()


def test_check_command_raw_output_flag(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
) -> None:
    """Verify --raw-output flag is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(check_command, ["--raw-output"])

    mock_run_lint_tools_check.assert_called_once()
    call_kwargs = mock_run_lint_tools_check.call_args.kwargs
    assert_that(call_kwargs["raw_output"]).is_true()


def test_check_command_tool_options(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
) -> None:
    """Verify --tool-options is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(
            check_command,
            ["--tool-options", "ruff:line-length=120"],
        )

    mock_run_lint_tools_check.assert_called_once()
    call_kwargs = mock_run_lint_tools_check.call_args.kwargs
    assert_that(call_kwargs["tool_options"]).is_equal_to("ruff:line-length=120")


def test_check_command_output_file(
    cli_runner: CliRunner,
    mock_run_lint_tools_check: MagicMock,
    tmp_path: Path,
) -> None:
    """Verify --output option is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_check: Mock for the run_lint_tools_check function.
        tmp_path: Temporary directory path for testing.
    """
    output_file = str(tmp_path / "results.json")
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(check_command, ["--output", output_file])

    mock_run_lint_tools_check.assert_called_once()
    call_kwargs = mock_run_lint_tools_check.call_args.kwargs
    assert_that(call_kwargs["output_file"]).is_equal_to(output_file)


# =============================================================================
# Programmatic check() Function Tests
# =============================================================================


def test_check_function_calls_command() -> None:
    """Verify check() routes through the library API."""
    with patch(
        "lintro.api.core.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        check(
            paths=("src",),
            tools="ruff",
            tool_options=None,
            exclude=None,
            include_venv=False,
            output=None,
            output_format="grid",
            group_by="file",
            ignore_conflicts=False,
            verbose=False,
            no_log=False,
        )

        mock_run.assert_called_once()


def test_check_function_exits_on_failure() -> None:
    """Verify check() function exits with non-zero code on failure."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=1):
        with pytest.raises(SystemExit) as exc_info:
            check(
                paths=("src",),
                tools="ruff",
                tool_options=None,
                exclude=None,
                include_venv=False,
                output=None,
                output_format="grid",
                group_by="file",
                ignore_conflicts=False,
                verbose=False,
                no_log=False,
            )

        assert_that(exc_info.value.code).is_equal_to(1)
