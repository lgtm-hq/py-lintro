"""Unit tests for CLI entrypoint command listing and aliases."""

from __future__ import annotations

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli

SUBCOMMANDS: tuple[str, ...] = (
    "check",
    "config",
    "doctor",
    "format",
    "init",
    "install",
    "licenses",
    "list-tools",
    "review",
    "setup",
    "test",
    "versions",
)


def test_cli_lists_commands_and_aliases() -> None:
    """Ensure help lists primary commands and their common aliases."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("check")
    assert_that(result.output).contains("format")
    assert_that(result.output).contains("list-tools")
    assert_that(result.output).contains("chk")
    assert_that(result.output).contains("fmt")
    assert_that(result.output).contains("ls")


@pytest.mark.parametrize("subcommand", SUBCOMMANDS)
def test_subcommand_help_has_no_raw_docstring_sections(subcommand: str) -> None:
    """Ensure --help omits Google-style Args:/Raises: docstring sections.

    Args:
        subcommand: Name of the CLI subcommand to inspect.
    """
    runner = CliRunner()
    result = runner.invoke(cli, [subcommand, "--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).does_not_contain("Args:")
    assert_that(result.output).does_not_contain("Raises:")


@pytest.mark.parametrize("subcommand", SUBCOMMANDS)
def test_subcommand_help_shows_summary_and_options(subcommand: str) -> None:
    """Ensure --help still renders the human summary and option list.

    Args:
        subcommand: Name of the CLI subcommand to inspect.
    """
    runner = CliRunner()
    result = runner.invoke(cli, [subcommand, "--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Usage:")
    assert_that(result.output).contains("--help")
