"""Tests for LintroGroup and CLI module functionality."""

from unittest.mock import patch

from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli


def test_format_commands_displays_canonical_names() -> None:
    """Test that format_commands displays canonical command names."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("check")
    assert_that(result.output).contains("format")
    assert_that(result.output).contains("test")
    assert_that(result.output).contains("list-tools")


def test_format_commands_with_aliases() -> None:
    """Test that format_commands includes aliases in help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    # The output should contain commands with or without aliases
    assert_that(result.exit_code).is_equal_to(0)
    # Check that the Commands table is displayed (Rich table format)
    assert_that(result.output).contains("Commands")
    assert_that(result.output).contains("Alias")


def test_check_command_help_displays() -> None:
    """Test that check command help displays properly."""
    runner = CliRunner()
    result = runner.invoke(cli, ["check", "--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Check files for issues")


def test_format_command_help_displays() -> None:
    """Test that format command help displays properly."""
    runner = CliRunner()
    result = runner.invoke(cli, ["format", "--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Format code")


def test_test_command_help_displays() -> None:
    """Test that test command help displays properly."""
    runner = CliRunner()
    result = runner.invoke(cli, ["test", "--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Run tests")


def test_list_tools_command_help_displays() -> None:
    """Test that list-tools command help displays properly."""
    runner = CliRunner()
    result = runner.invoke(cli, ["list-tools", "--help"])
    assert_that(result.exit_code).is_equal_to(0)


def test_invoke_single_command_execution() -> None:
    """Test that invoke executes single command correctly."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.check.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        result = runner.invoke(cli, ["check", "."])
        assert_that(result.exit_code).is_equal_to(0)
        mock_run.assert_called_once()


def test_invoke_with_comma_separated_commands() -> None:
    """Test that invoke handles comma-separated command chaining."""
    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.check.run_lint_tools_simple") as mock_check,
        patch("lintro.cli_utils.commands.format.run_lint_tools_simple") as mock_fmt,
    ):
        mock_check.return_value = 0
        mock_fmt.return_value = 0
        result = runner.invoke(cli, ["check", ".", ",", "format", "."])
        # Verify exit code is success
        assert_that(result.exit_code).is_equal_to(0)
        # Both commands should have been called exactly once
        assert_that(mock_check.call_count).is_equal_to(1)
        assert_that(mock_fmt.call_count).is_equal_to(1)
        # Verify both commands were called with the expected path argument
        mock_check.assert_any_call(
            action="check",
            paths=["."],
            tools=None,
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="file",
            output_format="grid",
            verbose=False,
            raw_output=False,
            output_file=None,
            incremental=False,
            debug=False,
            stream=False,
            no_log=False,
            auto_install=False,
            yes=False,
            ai_fix=False,
        )
        mock_fmt.assert_any_call(
            action="fmt",
            paths=["."],
            tools=None,
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="auto",
            output_format="grid",
            verbose=False,
            raw_output=False,
            output_file=None,
            debug=False,
            stream=False,
            no_log=False,
            auto_install=False,
            yes=False,
        )


def test_invoke_aggregates_exit_codes_success() -> None:
    """Test that invoke aggregates exit codes from chained commands."""
    runner = CliRunner()
    with patch("lintro.tools.tool_manager.get_all_tools") as mock_get:
        mock_get.return_value = {}
        result = runner.invoke(cli, ["list-tools"])
        # Should return 0 when command succeeds
        assert_that(result.exit_code).is_equal_to(0)


def test_invoke_with_version_flag() -> None:
    """Test that invoke handles --version flag."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("version")


def test_invoke_with_help_flag() -> None:
    """Test that invoke handles --help flag."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Lintro")


def test_invoke_without_command_shows_help() -> None:
    """Test that invoking cli without command succeeds."""
    runner = CliRunner()
    result = runner.invoke(cli, [])
    # Should succeed (exit code 0) when invoked without command
    assert_that(result.exit_code).is_equal_to(0)


def test_invoke_with_invalid_command() -> None:
    """Test that invoke handles invalid commands."""
    runner = CliRunner()
    result = runner.invoke(cli, ["invalid-command"])
    assert_that(result.exit_code).is_not_equal_to(0)


def test_invoke_command_not_found() -> None:
    """Test error handling for non-existent command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["nonexistent"])
    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("No such command")


def test_chaining_ignores_empty_command_groups() -> None:
    """Test that chaining ignores empty command groups."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.check.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        # Multiple commas in a row would create empty groups
        result = runner.invoke(cli, ["check", ".", ",", ",", "check", "."])
        # Should still execute the check commands, ignoring empty groups
        assert_that(result.exit_code).is_equal_to(0)
        assert_that(mock_run.call_count).is_equal_to(2)
        # Verify both calls were for the expected path
        assert_that(mock_run.call_args_list[0][1]["paths"]).is_equal_to(["."])
        assert_that(mock_run.call_args_list[1][1]["paths"]).is_equal_to(["."])


def test_chaining_with_flags() -> None:
    """Test that chaining preserves flags for each command."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.check.run_lint_tools_simple") as mock_run:
        mock_run.return_value = 0
        result = runner.invoke(
            cli,
            ["check", ".", "--tools", "ruff", ",", "check", "."],
        )
        # Verify exit code is success
        assert_that(result.exit_code).is_equal_to(0)
        # Both commands should have been called
        assert_that(mock_run.call_count).is_equal_to(2)
        # Validate first call preserves the --tools flag
        assert_that(mock_run.call_args_list[0][1]["paths"]).is_equal_to(["."])
        assert_that(mock_run.call_args_list[0][1]["tools"]).is_equal_to("ruff")
        # Validate second call reflects the second command invocation with default tools
        assert_that(mock_run.call_args_list[1][1]["paths"]).is_equal_to(["."])
        assert_that(mock_run.call_args_list[1][1]["tools"]).is_none()


def test_invoke_with_realistic_comma_separated_inputs() -> None:
    """Test that invoke handles realistic comma-separated command inputs.

    Tests inputs like fmt,chk. This test intentionally uses the single-token
    "fmt,chk" syntax (as opposed to the multi-argument comma syntax used elsewhere).
    """
    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.format.run_lint_tools_simple") as mock_fmt,
        patch("lintro.cli_utils.commands.check.run_lint_tools_simple") as mock_check,
    ):
        mock_fmt.return_value = 0
        mock_check.return_value = 0
        # Test realistic input: fmt,chk (comma-separated in single token)
        result = runner.invoke(cli, ["fmt,chk", "."])
        assert_that(result.exit_code).is_equal_to(0)
        # Both commands should have been called exactly once
        assert_that(mock_fmt.call_count).is_equal_to(1)
        assert_that(mock_check.call_count).is_equal_to(1)


def test_invoke_with_chk_tst_input() -> None:
    """Test that invoke handles chk,tst style input."""
    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.check.run_lint_tools_simple") as mock_check,
        patch("lintro.cli_utils.commands.test.run_lint_tools_simple") as mock_test,
    ):
        mock_check.return_value = 0
        mock_test.return_value = 0
        # Test realistic input: chk,tst (comma-separated in single token)
        result = runner.invoke(cli, ["chk,tst", "."])
        assert_that(result.exit_code).is_equal_to(0)
        # Both commands should have been called exactly once
        assert_that(mock_check.call_count).is_equal_to(1)
        assert_that(mock_test.call_count).is_equal_to(1)


def test_invoke_handles_system_exit() -> None:
    """Test that invoke properly handles SystemExit exceptions."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.check.run_lint_tools_simple") as mock_run:
        mock_run.side_effect = SystemExit(1)
        result = runner.invoke(cli, ["check", "."])
        # Should handle SystemExit gracefully
        assert_that(isinstance(result.exception, SystemExit)).is_true()
        assert_that(result.exit_code).is_equal_to(1)
        mock_run.assert_called_once()


def test_invoke_preserves_max_exit_code() -> None:
    """Test that chained command execution preserves the max exit code."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.check.run_lint_tools_simple") as mock_run:
        # Simulate different exit codes from multiple runs
        mock_run.side_effect = [0, 1]
        result = runner.invoke(cli, ["check", ".", ",", "check", "."])
        # Result should reflect the maximum exit code (1)
        assert_that(result.exit_code).is_equal_to(1)


def test_invoke_with_exception_in_command() -> None:
    """Test error handling when command raises exception."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.check.run_lint_tools_simple") as mock_run:
        mock_run.side_effect = Exception("Test error")
        result = runner.invoke(cli, ["check", "."])
        # Should handle exceptions gracefully with non-zero exit code
        assert_that(result.exit_code).is_not_equal_to(0)
        # Verify the mocked function was called
        mock_run.assert_called_once()
