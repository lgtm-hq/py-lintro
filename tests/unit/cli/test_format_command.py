"""Unit tests for lintro/cli_utils/commands/format.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.format import format_code, format_command

# =============================================================================
# Format Command Basic Tests
# =============================================================================


def test_format_command_help(cli_runner: CliRunner) -> None:
    """Verify format command shows help.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(format_command, ["--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Format")


def test_format_command_default_paths(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
) -> None:
    """Verify format command uses default paths when none provided.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(format_command, [])

    mock_run_lint_tools_format.assert_called_once()
    call_kwargs = mock_run_lint_tools_format.call_args.kwargs
    assert_that(call_kwargs["paths"]).is_equal_to(["."])


def test_format_command_with_paths(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
    tmp_path: Path,
) -> None:
    """Verify format command passes provided paths.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
        tmp_path: Temporary directory path for testing.
    """
    test_file = tmp_path / "test.py"
    test_file.write_text("# test")

    cli_runner.invoke(format_command, [str(test_file)])

    mock_run_lint_tools_format.assert_called_once()
    call_kwargs = mock_run_lint_tools_format.call_args.kwargs
    assert_that(call_kwargs["paths"]).contains(str(test_file))


def test_format_command_exit_code_zero_on_success(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
) -> None:
    """Verify format command exits with 0 on success.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
    """
    mock_run_lint_tools_format.return_value = 0
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, [])

    assert_that(result.exit_code).is_equal_to(0)


def test_format_command_exit_code_nonzero_on_error(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
) -> None:
    """Verify format command exits with non-zero on error.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
    """
    mock_run_lint_tools_format.return_value = 1
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, [])

    assert_that(result.exit_code).is_equal_to(1)


# =============================================================================
# Format Command Options Tests
# =============================================================================


def test_format_command_tools_option(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
) -> None:
    """Verify --tools option is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(format_command, ["--tools", "ruff,black"])

    mock_run_lint_tools_format.assert_called_once()
    call_kwargs = mock_run_lint_tools_format.call_args.kwargs
    assert_that(call_kwargs["tools"]).is_equal_to("ruff,black")


def test_format_command_exclude_option(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
) -> None:
    """Verify --exclude option is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(format_command, ["--exclude", "*.pyc,__pycache__"])

    mock_run_lint_tools_format.assert_called_once()
    call_kwargs = mock_run_lint_tools_format.call_args.kwargs
    assert_that(call_kwargs["exclude"]).is_equal_to("*.pyc,__pycache__")


def test_format_command_include_venv_flag(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
) -> None:
    """Verify --include-venv flag is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(format_command, ["--include-venv"])

    mock_run_lint_tools_format.assert_called_once()
    call_kwargs = mock_run_lint_tools_format.call_args.kwargs
    assert_that(call_kwargs["include_venv"]).is_true()


def test_format_command_output_format_option(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
) -> None:
    """Verify --output-format option is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(format_command, ["--output-format", "json"])

    mock_run_lint_tools_format.assert_called_once()
    call_kwargs = mock_run_lint_tools_format.call_args.kwargs
    assert_that(call_kwargs["output_format"]).is_equal_to("json")


@pytest.mark.parametrize(
    "format_option",
    ["plain", "grid", "markdown", "html", "json", "csv"],
    ids=["plain", "grid", "markdown", "html", "json", "csv"],
)
def test_format_command_output_format_valid_choices(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
    format_option: str,
) -> None:
    """Verify all valid output format choices are accepted.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
        format_option: The output format option being tested.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--output-format", format_option])

    assert_that(result.exit_code).is_equal_to(0)


def test_format_command_group_by_option(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
) -> None:
    """Verify --group-by option is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(format_command, ["--group-by", "code"])

    mock_run_lint_tools_format.assert_called_once()
    call_kwargs = mock_run_lint_tools_format.call_args.kwargs
    assert_that(call_kwargs["group_by"]).is_equal_to("code")


@pytest.mark.parametrize(
    "group_by_option",
    ["file", "code", "none", "auto"],
    ids=["file", "code", "none", "auto"],
)
def test_format_command_group_by_valid_choices(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
    group_by_option: str,
) -> None:
    """Verify all valid group-by choices are accepted.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
        group_by_option: The group-by option being tested.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--group-by", group_by_option])

    assert_that(result.exit_code).is_equal_to(0)


def test_format_command_verbose_flag(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
) -> None:
    """Verify --verbose flag is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(format_command, ["--verbose"])

    mock_run_lint_tools_format.assert_called_once()
    call_kwargs = mock_run_lint_tools_format.call_args.kwargs
    assert_that(call_kwargs["verbose"]).is_true()


def test_format_command_verbose_short_flag(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
) -> None:
    """Verify -v short flag works for verbose.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(format_command, ["-v"])

    mock_run_lint_tools_format.assert_called_once()
    call_kwargs = mock_run_lint_tools_format.call_args.kwargs
    assert_that(call_kwargs["verbose"]).is_true()


def test_format_command_raw_output_flag(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
) -> None:
    """Verify --raw-output flag is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(format_command, ["--raw-output"])

    mock_run_lint_tools_format.assert_called_once()
    call_kwargs = mock_run_lint_tools_format.call_args.kwargs
    assert_that(call_kwargs["raw_output"]).is_true()


def test_format_command_tool_options(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
) -> None:
    """Verify --tool-options is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(
            format_command,
            ["--tool-options", "ruff:line-length=120"],
        )

    mock_run_lint_tools_format.assert_called_once()
    call_kwargs = mock_run_lint_tools_format.call_args.kwargs
    assert_that(call_kwargs["tool_options"]).is_equal_to("ruff:line-length=120")


def test_format_command_uses_fmt_action(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
) -> None:
    """Verify format command uses 'fmt' action.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
    """
    with cli_runner.isolated_filesystem():
        cli_runner.invoke(format_command, [])

    mock_run_lint_tools_format.assert_called_once()
    call_kwargs = mock_run_lint_tools_format.call_args.kwargs
    assert_that(call_kwargs["action"]).is_equal_to("fmt")


# =============================================================================
# Programmatic format_code() Function Tests
# =============================================================================


def test_format_code_function_calls_command() -> None:
    """Verify format_code() routes through the library API."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        format_code(
            paths=["src"],
            tools="ruff",
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="auto",
            output_format="grid",
            verbose=False,
        )

        mock_run.assert_called_once()


def test_format_code_function_raises_on_failure() -> None:
    """Verify format_code() function raises RuntimeError on failure."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=1):
        with pytest.raises(RuntimeError) as exc_info:
            format_code(
                paths=["src"],
                tools="ruff",
            )

        assert_that(str(exc_info.value)).contains("Format failed")


def test_format_code_function_default_parameters() -> None:
    """Verify format_code() function uses default parameters."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        # Call with only required parameters (all have defaults)
        format_code()

        mock_run.assert_called_once()
        # Verify defaults were used (no tools means tools=None)
        assert_that(mock_run.call_args.kwargs["tools"]).is_none()


def test_format_code_function_with_all_options() -> None:
    """Verify format_code() passes all options correctly."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        format_code(
            paths=["src", "tests"],
            tools="ruff,black",
            tool_options="ruff:line-length=100",
            exclude="*.pyc",
            include_venv=True,
            group_by="file",
            output_format="json",
            verbose=True,
        )

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args.kwargs

        assert_that(call_kwargs["paths"]).contains("src")
        assert_that(call_kwargs["paths"]).contains("tests")
        assert_that(call_kwargs["tools"]).is_equal_to("ruff,black")
        assert_that(call_kwargs["include_venv"]).is_true()
        assert_that(call_kwargs["verbose"]).is_true()


# =============================================================================
# Format Command Edge Cases
# =============================================================================


def test_format_command_invalid_output_format(cli_runner: CliRunner) -> None:
    """Verify format command rejects invalid output format.

    Args:
        cli_runner: The Click CLI test runner.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--output-format", "invalid"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Invalid value")


def test_format_command_invalid_group_by(cli_runner: CliRunner) -> None:
    """Verify format command rejects invalid group-by option.

    Args:
        cli_runner: The Click CLI test runner.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--group-by", "invalid"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Invalid value")


def test_format_command_multiple_paths(
    cli_runner: CliRunner,
    mock_run_lint_tools_format: MagicMock,
    tmp_path: Path,
) -> None:
    """Verify format command handles multiple paths.

    Args:
        cli_runner: The Click CLI test runner.
        mock_run_lint_tools_format: Mock for the run_lint_tools_format function.
        tmp_path: Temporary directory path for testing.
    """
    file1 = tmp_path / "file1.py"
    file2 = tmp_path / "file2.py"
    file1.write_text("# file1")
    file2.write_text("# file2")

    cli_runner.invoke(format_command, [str(file1), str(file2)])

    mock_run_lint_tools_format.assert_called_once()
    call_kwargs = mock_run_lint_tools_format.call_args.kwargs
    assert_that(len(call_kwargs["paths"])).is_equal_to(2)
