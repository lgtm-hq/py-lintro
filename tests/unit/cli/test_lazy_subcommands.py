"""Guards for the lazy-subcommand tables in :mod:`lintro.cli`.

``lintro --help`` renders its Commands table from static metadata so it never
imports a subcommand. That trades an import for three hand-maintained tables,
which these tests keep aligned with each other, with the real commands, and
with the user-visible help text.
"""

from __future__ import annotations

import click
import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import (
    _CANONICAL_NAMES,
    _COMMAND_SHORT_HELP,
    _LAZY_SUBCOMMANDS,
    SHORT_HELP_LIMIT,
    cli,
)

_CANONICAL_COMMANDS: tuple[str, ...] = tuple(sorted(set(_CANONICAL_NAMES.values())))


def test_lazy_and_canonical_tables_cover_the_same_names() -> None:
    """Every lazy command name has a canonical name, and vice versa."""
    assert_that(set(_LAZY_SUBCOMMANDS)).is_equal_to(set(_CANONICAL_NAMES))


def test_canonical_names_have_short_help() -> None:
    """Canonical command names and short-help keys match exactly."""
    assert_that(set(_CANONICAL_NAMES.values())).is_equal_to(set(_COMMAND_SHORT_HELP))


def test_canonical_names_are_self_mapping() -> None:
    """Each canonical name maps to itself, so aliases resolve to a real command."""
    for canonical in _CANONICAL_COMMANDS:
        assert_that(_CANONICAL_NAMES).contains_key(canonical)
        assert_that(_CANONICAL_NAMES[canonical]).is_equal_to(canonical)


def test_aliases_share_the_canonical_import_path() -> None:
    """An alias imports the same object as the command it aliases."""
    for name, canonical in _CANONICAL_NAMES.items():
        assert_that(_LAZY_SUBCOMMANDS[name]).is_equal_to(_LAZY_SUBCOMMANDS[canonical])


@pytest.mark.parametrize("canonical", _CANONICAL_COMMANDS)
def test_get_command_registers_canonical_name(canonical: str) -> None:
    """``get_command`` resolves the name users type, not Click's auto-name.

    Args:
        canonical: Canonical command name under test.
    """
    ctx = click.Context(cli)
    command = cli.get_command(ctx, canonical)
    assert_that(isinstance(command, click.Command)).is_true()
    assert command is not None
    assert_that(command.name).is_equal_to(canonical)
    assert_that(cli.list_commands(ctx)).contains(canonical)
    assert_that(cli.commands).contains_key(canonical)


def test_aliases_resolve_to_the_canonical_command_object() -> None:
    """Aliases and canonical names resolve to the same loaded command."""
    ctx = click.Context(cli)
    for name, canonical in _CANONICAL_NAMES.items():
        loaded = cli.get_command(ctx, name)
        canonical_cmd = cli.get_command(ctx, canonical)
        assert_that(loaded).is_same_as(canonical_cmd)
        assert loaded is not None
        assert_that(loaded.name).is_equal_to(canonical)


@pytest.mark.parametrize("canonical", _CANONICAL_COMMANDS)
def test_static_short_help_matches_the_command(canonical: str) -> None:
    """The static help table matches what Click would render at runtime.

    Args:
        canonical: Canonical command name under test.
    """
    ctx = click.Context(cli)
    command = cli.get_command(ctx, canonical)
    assert command is not None
    assert_that(_COMMAND_SHORT_HELP[canonical]).is_equal_to(
        command.get_short_help_str(limit=SHORT_HELP_LIMIT),
    )


def test_short_help_entries_fit_the_render_limit() -> None:
    """No table entry is long enough to be truncated when rendered."""
    for canonical, short_help in _COMMAND_SHORT_HELP.items():
        assert_that(len(short_help)).described_as(canonical).is_less_than_or_equal_to(
            SHORT_HELP_LIMIT,
        )


def test_root_help_lists_canonical_names_aliases_and_descriptions() -> None:
    """``lintro --help`` shows each canonical name, alias, and description."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert_that(result.exit_code).is_equal_to(0)
    normalized = " ".join(result.output.split())
    for name, canonical in _CANONICAL_NAMES.items():
        assert_that(result.output).contains(name)
        assert_that(normalized).contains(_COMMAND_SHORT_HELP[canonical])
