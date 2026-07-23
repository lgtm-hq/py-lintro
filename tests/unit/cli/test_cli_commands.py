"""Unit tests for CLI entrypoint command listing and aliases."""

from __future__ import annotations

import re

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli

SUBCOMMANDS: tuple[str, ...] = (
    "check",
    "completions",
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

# Human-facing summary phrases that must survive Click's \\f truncation.
SUBCOMMAND_SUMMARY_PHRASES: dict[str, str] = {
    "check": "Check files for issues using the specified tools.",
    "completions": "Print a shell completion script for bash, zsh, or fish.",
    "config": "Display Lintro configuration status.",
    "doctor": "Check tool installation status and version compatibility.",
    "format": "Format code using configured formatting tools.",
    "init": "Initialize Lintro configuration for your project.",
    "install": "Install or upgrade external tools used by lintro.",
    "licenses": "Check dependency licenses for policy compliance.",
    "list-tools": "List all available tools and their configurations.",
    "review": "Run AI-powered diff-based code review.",
    "setup": "Set up lintro for your project.",
    "test": "Run tests using pytest.",
    "versions": "Display version information for all supported tools.",
}

_DOCSTRING_SECTION_RE = re.compile(
    r"^\s*(Args|Raises|Returns|Note|Notes|Example|Examples|Yields|Attributes"
    r"|Warning|Warnings|See Also|References|Todo):",
    re.MULTILINE,
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
    """Ensure --help omits Google-style docstring section headers.

    Args:
        subcommand: Name of the CLI subcommand to inspect.
    """
    runner = CliRunner()
    result = runner.invoke(cli, [subcommand, "--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(_DOCSTRING_SECTION_RE.search(result.output)).is_none()


@pytest.mark.parametrize(
    ("subcommand", "summary"),
    sorted(SUBCOMMAND_SUMMARY_PHRASES.items()),
)
def test_subcommand_help_shows_summary_and_options(
    subcommand: str,
    summary: str,
) -> None:
    """Ensure --help still renders the human summary and option list.

    Args:
        subcommand: Name of the CLI subcommand to inspect.
        summary: Expected human-facing summary phrase from the docstring.
    """
    runner = CliRunner()
    result = runner.invoke(cli, [subcommand, "--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Usage:")
    assert_that(result.output).contains("--help")
    assert_that(result.output).contains(summary)
