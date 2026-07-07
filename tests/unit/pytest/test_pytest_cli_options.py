"""Tests for pytest CLI test command."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.test import test_command as pytest_cli_command


def test_test_command_help() -> None:
    """Test that test command shows help."""
    runner = CliRunner()
    result = runner.invoke(pytest_cli_command, ["--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Run tests using pytest")


def test_test_command_default_paths() -> None:
    """Test test command with default paths."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(pytest_cli_command, [])
        assert_that(mock_run.called).is_true()
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["paths"]).is_equal_to(["."])
        assert_that(call_args.kwargs["tools"]).is_equal_to("pytest")


def test_test_command_explicit_paths() -> None:
    """Test test command with explicit paths."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = Path(tmpdir)
        (test_dir / "test_file.py").write_text("def test(): pass\n")
        with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
            mock_run.return_value = 0
            runner.invoke(
                pytest_cli_command,
                [str(test_dir / "test_file.py")],
            )
            call_args = mock_run.call_args
            assert_that(call_args.kwargs["paths"]).contains(
                str(test_dir / "test_file.py"),
            )


def test_test_command_exclude_patterns() -> None:
    """Test test command with exclude patterns."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(
            pytest_cli_command,
            ["--exclude", "*.venv,__pycache__"],
        )
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["exclude"]).is_equal_to("*.venv,__pycache__")


def test_test_command_include_venv() -> None:
    """Test test command with include-venv flag."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(pytest_cli_command, ["--include-venv"])
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["include_venv"]).is_true()


def test_test_command_output_format() -> None:
    """Test test command with output format option."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(
            pytest_cli_command,
            ["--output-format", "json"],
        )
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["output_format"]).is_equal_to("json")


def test_test_command_group_by() -> None:
    """Test test command with group-by option."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(pytest_cli_command, ["--group-by", "code"])
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["group_by"]).is_equal_to("code")


def test_test_command_verbose() -> None:
    """Test test command with verbose flag."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(pytest_cli_command, ["--verbose"])
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["verbose"]).is_true()


def test_test_command_raw_output() -> None:
    """Test test command with raw-output flag."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(pytest_cli_command, ["--raw-output"])
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["raw_output"]).is_true()


def test_test_command_list_plugins() -> None:
    """Test test command with --list-plugins flag."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(pytest_cli_command, ["--list-plugins"])
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["tool_options"]).contains(
            "pytest:list_plugins=True",
        )


def test_test_command_check_plugins() -> None:
    """Test test command with --check-plugins flag."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(
            pytest_cli_command,
            [
                "--check-plugins",
                "--tool-options",
                "pytest:required_plugins=pytest-cov,pytest-xdist",
            ],
        )
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["tool_options"]).contains(
            "pytest:check_plugins=True",
        )
        assert_that(call_args.kwargs["tool_options"]).contains(
            "pytest:required_plugins=pytest-cov,pytest-xdist",
        )
