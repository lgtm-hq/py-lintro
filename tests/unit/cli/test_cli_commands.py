"""Unit tests for CLI entrypoint command listing and aliases."""

from __future__ import annotations

import re

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli

SUBCOMMANDS: tuple[str, ...] = (
    "badge",
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
    "watch",
)

# Human-facing summary phrases that must survive Click's \\f truncation.
SUBCOMMAND_SUMMARY_PHRASES: dict[str, str] = {
    "badge": "Generate a shields.io markdown badge for the project health score.",
    "check": "Check files for issues using the specified tools.",
    "completions": "Print a shell completion script for bash, zsh, or fish.",
    "doctor": "Check tool installation status and version compatibility.",
    "format": "Format code using configured formatting tools.",
    "init": "Initialize Lintro configuration for your project.",
    "install": "Install or upgrade external tools used by lintro.",
    "licenses": "Check dependency licenses for policy compliance.",
    "list-tools": "List all available tools and their configurations.",
    "review": "Run AI-powered diff-based code review, plus advisory AI finders.",
    "setup": "Set up lintro for your project.",
    "test": "Run tests using pytest.",
    "versions": "Display version information for all supported tools.",
    "watch": "Watch paths and continuously lint files as they change.",
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
    assert_that(result.output).contains("watch")
    assert_that(result.output).contains("w")


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


def test_config_help_lists_subcommands() -> None:
    """``lintro config --help`` must list the real subcommands, not a copied blurb.

    The group docstring is truncated at form-feed for Click; this checks the
    behavior users see (show / validate / init) rather than echoing the
    source docstring.
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Usage:")
    assert_that(result.output).contains("show")
    assert_that(result.output).contains("validate")
    assert_that(result.output).contains("init")
