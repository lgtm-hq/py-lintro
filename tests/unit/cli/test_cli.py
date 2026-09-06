"""Unit tests for lintro/cli.py - CLI entry point and LintroGroup."""

from __future__ import annotations

import contextlib
import io
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro import __version__
from lintro.cli import (
    LintroGroup,
    _is_utf8_encoding,
    cli,
    ensure_utf8_stdio,
    main,
)

# =============================================================================
# CLI Entry Point Tests
# =============================================================================


def test_cli_version_option(cli_runner: CliRunner) -> None:
    """Verify --version shows version and exits cleanly.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(cli, ["--version"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains(__version__)


def test_cli_help_option(cli_runner: CliRunner) -> None:
    """Verify --help shows help and exits cleanly.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(cli, ["--help"])

    assert_that(result.exit_code).is_equal_to(0)
    # Rich-formatted help contains "Lintro"
    assert_that(result.output).contains("Lintro")


def test_cli_no_command_shows_help(cli_runner: CliRunner) -> None:
    """Verify running cli without command shows help.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(cli, [])

    assert_that(result.exit_code).is_equal_to(0)


def test_cli_invalid_command(cli_runner: CliRunner) -> None:
    """Verify invalid command shows error.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(cli, ["nonexistent-command-xyz"])

    assert_that(result.exit_code).is_not_equal_to(0)


def test_main_entry_point_forces_utf8_before_running_the_cli() -> None:
    """main() reconfigures stdout to UTF-8 before the CLI writes to it.

    Drives a real ASCII text stream: a CLI body that emits a non-ASCII
    character would raise ``UnicodeEncodeError`` if ``ensure_utf8_stdio`` had
    not already run, so the encoded bytes landing in the buffer are proof of
    the ordering.
    """
    buffer = io.BytesIO()
    stdout = io.TextIOWrapper(buffer, encoding="ascii", errors="strict")

    def _fake_cli() -> None:
        """Stand in for the Click entry point and write a non-ASCII banner."""
        stdout.write("wrench \U0001f527")
        stdout.flush()

    with (
        patch("lintro.cli.sys.stdout", stdout),
        patch("lintro.cli.cli", _fake_cli),
    ):
        with contextlib.suppress(SystemExit):
            main()

    assert_that(stdout.encoding).is_equal_to("utf-8")
    assert_that(buffer.getvalue().decode("utf-8")).contains("wrench \U0001f527")


@pytest.mark.parametrize(
    ("encoding", "expected"),
    [
        ("utf-8", True),
        ("UTF-8", True),
        ("utf8", True),
        ("UTF8", True),
        ("utf_8", True),
        ("UTF_8", True),
        ("ascii", False),
        ("US-ASCII", False),
        ("latin-1", False),
        (None, False),
    ],
    ids=[
        "utf-8",
        "UTF-8",
        "utf8",
        "UTF8",
        "utf_8",
        "UTF_8",
        "ascii",
        "US-ASCII",
        "latin-1",
        "none",
    ],
)
def test_is_utf8_encoding(encoding: str | None, expected: bool) -> None:
    """Verify UTF-8 encoding name detection.

    Args:
        encoding: Encoding name under test.
        expected: Whether the name should be treated as UTF-8.
    """
    assert_that(_is_utf8_encoding(encoding)).is_equal_to(expected)


def test_ensure_utf8_stdio_reconfigures_ascii_streams() -> None:
    """ASCII stdout/stderr end up UTF-8 and able to carry non-ASCII text."""
    out_buffer = io.BytesIO()
    err_buffer = io.BytesIO()
    stdout = io.TextIOWrapper(out_buffer, encoding="ascii", errors="strict")
    stderr = io.TextIOWrapper(err_buffer, encoding="US-ASCII", errors="strict")

    with (
        patch("lintro.cli.sys.stdout", stdout),
        patch("lintro.cli.sys.stderr", stderr),
    ):
        ensure_utf8_stdio()

    assert_that(stdout.encoding).is_equal_to("utf-8")
    assert_that(stderr.encoding).is_equal_to("utf-8")
    assert_that(stdout.errors).is_equal_to("replace")
    assert_that(stderr.errors).is_equal_to("replace")

    stdout.write("\U0001f527")
    stdout.flush()
    assert_that(out_buffer.getvalue().decode("utf-8")).is_equal_to("\U0001f527")


def test_ensure_utf8_stdio_skips_utf8_streams() -> None:
    """Already-UTF-8 streams keep their own error handler untouched.

    A reconfigure would force ``errors="replace"``, so the surviving
    ``strict`` handler is the observable proof that the streams were left
    alone.
    """
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    stderr = io.TextIOWrapper(io.BytesIO(), encoding="UTF-8", errors="strict")

    with (
        patch("lintro.cli.sys.stdout", stdout),
        patch("lintro.cli.sys.stderr", stderr),
    ):
        ensure_utf8_stdio()

    assert_that(stdout.errors).is_equal_to("strict")
    assert_that(stderr.errors).is_equal_to("strict")


def test_ensure_utf8_stdio_tolerates_streams_without_reconfigure() -> None:
    """Streams lacking reconfigure (e.g. StringIO) must not raise."""
    stdout = io.StringIO()
    stderr = io.StringIO()

    with (
        patch("lintro.cli.sys.stdout", stdout),
        patch("lintro.cli.sys.stderr", stderr),
    ):
        ensure_utf8_stdio()


# =============================================================================
# LintroGroup Tests
# =============================================================================


def test_lintro_group_format_help_includes_commands() -> None:
    """Verify LintroGroup.format_help includes registered commands."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    # Should contain command names
    assert_that(result.output).contains("check")
    assert_that(result.output).contains("format")


def test_lintro_group_format_help_includes_aliases() -> None:
    """Verify LintroGroup.format_help shows command aliases."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    # Should contain aliases
    assert_that(result.output).contains("chk")
    assert_that(result.output).contains("fmt")


def test_lintro_group_format_commands_empty() -> None:
    """Verify format_commands method exists for compatibility."""
    import click

    group = LintroGroup()
    ctx = click.Context(cli)
    formatter = click.HelpFormatter()

    # Should not raise
    group.format_commands(ctx, formatter)


# =============================================================================
# Command Registration Tests
# =============================================================================


def test_cli_has_check_command(cli_runner: CliRunner) -> None:
    """Verify check command is registered.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(cli, ["check", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Check files")


def test_cli_has_format_command(cli_runner: CliRunner) -> None:
    """Verify format command is registered.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(cli, ["format", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Format")


def test_cli_has_test_command(cli_runner: CliRunner) -> None:
    """Verify test command is registered.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(cli, ["test", "--help"])

    assert_that(result.exit_code).is_equal_to(0)


def test_cli_has_config_command(cli_runner: CliRunner) -> None:
    """Verify config command is registered.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(cli, ["config", "--help"])

    assert_that(result.exit_code).is_equal_to(0)


def test_cli_has_versions_command(cli_runner: CliRunner) -> None:
    """Verify versions command is registered.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(cli, ["versions", "--help"])

    assert_that(result.exit_code).is_equal_to(0)


def test_cli_has_badge_command(cli_runner: CliRunner) -> None:
    """Verify badge command is registered.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(cli, ["badge", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains(
        "Generate a shields.io markdown badge for the project's issue counts.",
    )


def test_cli_has_list_tools_command(cli_runner: CliRunner) -> None:
    """Verify list-tools command is registered.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(cli, ["list-tools", "--help"])

    assert_that(result.exit_code).is_equal_to(0)


def test_cli_has_init_command(cli_runner: CliRunner) -> None:
    """Verify init command is registered.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(cli, ["init", "--help"])

    assert_that(result.exit_code).is_equal_to(0)


# =============================================================================
# Command Alias Tests
# =============================================================================


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("chk", "check"),
        ("fmt", "format"),
        ("tst", "test"),
        ("cfg", "config"),
        ("ver", "versions"),
        ("ls", "list-tools"),
        ("w", "watch"),
    ],
    ids=[
        "chk->check",
        "fmt->format",
        "tst->test",
        "cfg->config",
        "ver->versions",
        "ls->list-tools",
        "w->watch",
    ],
)
def test_cli_alias_resolves_to_command(
    cli_runner: CliRunner,
    alias: str,
    canonical: str,
) -> None:
    """Verify command aliases resolve to canonical commands.

    Args:
        cli_runner: The Click CLI test runner.
        alias: The alias command name.
        canonical: The canonical command name.
    """
    result = cli_runner.invoke(cli, [alias, "--help"])

    assert_that(result.exit_code).is_equal_to(0)


# =============================================================================
# Command Chaining Tests
# =============================================================================


def test_lintro_group_invoke_single_command() -> None:
    """A single command's pipeline exit code reaches the process exit code."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock:
        mock.return_value = 2
        result = runner.invoke(cli, ["check", "."])

    assert_that(result.exit_code).is_equal_to(2)


def test_lintro_group_invoke_handles_keyboard_interrupt() -> None:
    """Verify KeyboardInterrupt is re-raised during command chaining."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock:
        mock.side_effect = KeyboardInterrupt()
        result = runner.invoke(cli, ["check", "."])
        # CliRunner catches KeyboardInterrupt and sets exit code
        assert_that(result.exit_code).is_not_equal_to(0)


def test_lintro_group_invoke_aggregates_exit_codes() -> None:
    """Verify chained commands aggregate exit codes (max)."""
    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.format.run_lint_with_ai") as mock_fmt,
        patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock_chk,
    ):
        # First command succeeds, second fails
        mock_fmt.return_value = 0
        mock_chk.return_value = 1
        result = runner.invoke(cli, ["fmt", ",", "chk"])
        # Result should be max of exit codes
        assert_that(result.exit_code).is_equal_to(1)
