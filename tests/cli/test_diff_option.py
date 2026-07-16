"""CLI wiring tests for the ``--diff`` option on check and format."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.utils.git_diff import DIFF_DEFAULT_SENTINEL


@pytest.mark.parametrize("command", ["chk", "fmt"])
def test_diff_option_in_help(command: str) -> None:
    """The ``--diff`` option is documented in check and format help.

    Args:
        command: CLI subcommand alias under test.
    """
    runner = CliRunner()
    result = runner.invoke(cli, [command, "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("--diff")


def test_diff_flag_without_value_passes_sentinel() -> None:
    """``chk --diff`` forwards the default sentinel to the runner."""
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        runner.invoke(cli, ["chk", "--diff", "--tools", "ruff"])

    assert_that(mock_run.call_args.kwargs["diff_base"]).is_equal_to(
        DIFF_DEFAULT_SENTINEL,
    )


def test_diff_flag_with_explicit_base_passes_ref() -> None:
    """``chk --diff main`` forwards the explicit base ref."""
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        runner.invoke(cli, ["chk", "--diff", "main", "--tools", "ruff"])

    assert_that(mock_run.call_args.kwargs["diff_base"]).is_equal_to("main")


def test_no_diff_flag_passes_none() -> None:
    """Omitting ``--diff`` forwards ``None`` (full scan)."""
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        runner.invoke(cli, ["chk", "--tools", "ruff"])

    assert_that(mock_run.call_args.kwargs["diff_base"]).is_none()


def test_format_diff_flag_passes_sentinel() -> None:
    """``fmt --diff`` forwards the default sentinel to the runner."""
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.format.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        runner.invoke(cli, ["fmt", "--diff", "--tools", "ruff"])

    assert_that(mock_run.call_args.kwargs["diff_base"]).is_equal_to(
        DIFF_DEFAULT_SENTINEL,
    )
