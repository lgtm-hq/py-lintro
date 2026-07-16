"""Tests that subcommand --help omits Google-style docstring sections."""

from __future__ import annotations

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli

# Canonical subcommand names registered on the root CLI group.
_SUBCOMMANDS: tuple[str, ...] = (
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


@pytest.mark.parametrize("subcommand", _SUBCOMMANDS)
def test_subcommand_help_omits_args_and_raises(subcommand: str) -> None:
    """Subcommand --help must not dump Args:/Raises: docstring sections.

    Args:
        subcommand: Canonical CLI subcommand name under test.
    """
    runner = CliRunner()
    result = runner.invoke(cli, [subcommand, "--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).does_not_contain("Args:")
    assert_that(result.output).does_not_contain("Raises:")
