"""Unit tests for pytest CLI command options."""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.test import test_command as pytest_cli_command


def test_test_command_collect_only() -> None:
    """Test test command with --collect-only flag."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(pytest_cli_command, ["--collect-only"])
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["tool_options"]).contains(
            "pytest:collect_only=True",
        )


def test_test_command_fixtures() -> None:
    """Test test command with --fixtures flag."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(pytest_cli_command, ["--fixtures"])
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["tool_options"]).contains(
            "pytest:list_fixtures=True",
        )


def test_test_command_fixture_info() -> None:
    """Test test command with --fixture-info flag."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(pytest_cli_command, ["--fixture-info", "sample_data"])
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["tool_options"]).contains(
            "pytest:fixture_info=sample_data",
        )


def test_test_command_markers() -> None:
    """Test test command with --markers flag."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(pytest_cli_command, ["--markers"])
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["tool_options"]).contains(
            "pytest:list_markers=True",
        )


def test_test_command_parametrize_help() -> None:
    """Test test command with --parametrize-help flag."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(pytest_cli_command, ["--parametrize-help"])
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["tool_options"]).contains(
            "pytest:parametrize_help=True",
        )


def test_test_command_coverage_options() -> None:
    """Test test command with coverage report options."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(
            pytest_cli_command,
            [
                "--tool-options",
                "pytest:coverage_html=htmlcov,pytest:coverage_xml=coverage.xml",
            ],
        )
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["tool_options"]).contains(
            "pytest:coverage_html=htmlcov",
        )
        assert_that(call_args.kwargs["tool_options"]).contains(
            "pytest:coverage_xml=coverage.xml",
        )


def test_test_command_multiple_new_flags() -> None:
    """Test test command with multiple new flags."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(
            pytest_cli_command,
            [
                "--list-plugins",
                "--markers",
                "--collect-only",
            ],
        )
        call_args = mock_run.call_args
        tool_options = call_args.kwargs["tool_options"]
        assert_that(tool_options).contains("pytest:list_plugins=True")
        assert_that(tool_options).contains("pytest:list_markers=True")
        assert_that(tool_options).contains("pytest:collect_only=True")


def test_test_command_tool_options_without_prefix() -> None:
    """Test test command with tool options without pytest: prefix."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(
            pytest_cli_command,
            ["--tool-options", "verbose=true,tb=long"],
        )
        call_args = mock_run.call_args
        tool_opts = call_args.kwargs["tool_options"]
        assert_that(tool_opts).contains("pytest:verbose=true")
        assert_that(tool_opts).contains("pytest:tb=long")


def test_test_command_tool_options_with_prefix() -> None:
    """Test test command with tool options already prefixed."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(
            pytest_cli_command,
            ["--tool-options", "pytest:verbose=true"],
        )
        call_args = mock_run.call_args
        tool_opts = call_args.kwargs["tool_options"]
        assert_that(tool_opts).is_equal_to("pytest:verbose=true")


def test_test_command_tool_options_mixed() -> None:
    """Test test command with mixed prefixed and unprefixed tool options."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(
            pytest_cli_command,
            ["--tool-options", "verbose=true,pytest:tb=long"],
        )
        call_args = mock_run.call_args
        tool_opts = call_args.kwargs["tool_options"]
        assert_that(tool_opts).contains("pytest:verbose=true")
        assert_that(tool_opts).contains("pytest:tb=long")


def test_test_command_exit_code_success() -> None:
    """Test test command propagates success exit code."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        result = runner.invoke(pytest_cli_command, [])
        assert_that(result.exit_code).is_equal_to(0)


def test_test_command_exit_code_failure() -> None:
    """Test test command propagates failure exit code."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 1
        result = runner.invoke(pytest_cli_command, [])
        assert_that(result.exit_code).is_equal_to(1)


def test_test_command_combined_options() -> None:
    """Test test command with multiple options combined."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        runner.invoke(
            pytest_cli_command,
            [
                ".",
                "--exclude",
                "*.venv",
                "--include-venv",
                "--output-format",
                "markdown",
                "--group-by",
                "file",
                "--verbose",
                "--raw-output",
                "--tool-options",
                "maxfail=5",
            ],
        )
        call_args = mock_run.call_args
        assert_that(call_args.kwargs["exclude"]).is_equal_to("*.venv")
        assert_that(call_args.kwargs["include_venv"]).is_true()
        assert_that(call_args.kwargs["output_format"]).is_equal_to("markdown")
        assert_that(call_args.kwargs["group_by"]).is_equal_to("file")
        assert_that(call_args.kwargs["verbose"]).is_true()
        assert_that(call_args.kwargs["raw_output"]).is_true()
        assert_that(call_args.kwargs["tool_options"]).contains("pytest:maxfail=5")
