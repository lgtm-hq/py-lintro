"""Tests that subcommand --help omits Google-style docstring sections."""

from __future__ import annotations

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli

# Canonical subcommand names and a distinctive help snippet for each.
_SUBCOMMAND_HELP: tuple[tuple[str, str], ...] = (
    ("check", "Check files for issues using the specified tools."),
    ("config", "Display Lintro configuration status."),
    ("doctor", "Check tool installation status and version compatibility."),
    ("format", "Format code using configured formatting tools."),
    ("init", "Initialize Lintro configuration for your project."),
    ("install", "Install or upgrade external tools used by lintro."),
    ("licenses", "Check dependency licenses for policy compliance."),
    ("list-tools", "List all available tools and their configurations."),
    ("review", "Run AI-powered diff-based code review."),
    ("setup", "Set up lintro for your project."),
    ("test", "Run tests using pytest."),
    ("versions", "Display version information for all supported tools."),
)


@pytest.mark.parametrize(
    ("subcommand", "help_snippet"),
    _SUBCOMMAND_HELP,
    ids=[name for name, _ in _SUBCOMMAND_HELP],
)
def test_subcommand_help_omits_args_and_raises(
    subcommand: str,
    help_snippet: str,
) -> None:
    """Subcommand --help must show human text without Args:/Raises: dumps.

    Args:
        subcommand: Canonical CLI subcommand name under test.
        help_snippet: Expected first-paragraph help text for the subcommand.
    """
    runner = CliRunner()
    result = runner.invoke(cli, [subcommand, "--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains(help_snippet)
    assert_that(result.output).does_not_contain("Args:")
    assert_that(result.output).does_not_contain("Raises:")
