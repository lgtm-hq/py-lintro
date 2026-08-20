"""Guards for the lazy-subcommand tables in :mod:`lintro.cli`.

``lintro --help`` renders its Commands table from static metadata so it never
imports a subcommand. That trades an import for three hand-maintained tables,
which these tests keep aligned with each other and with the real commands.
"""

from __future__ import annotations

import importlib

import click
import pytest
from assertpy import assert_that

from lintro.cli import (
    _CANONICAL_NAMES,
    _COMMAND_SHORT_HELP,
    _LAZY_SUBCOMMANDS,
    SHORT_HELP_LIMIT,
)

_CANONICAL_COMMANDS: tuple[str, ...] = tuple(sorted(set(_CANONICAL_NAMES.values())))


def _load_command(canonical: str) -> click.Command:
    """Import the Click command backing a canonical command name.

    Args:
        canonical: Canonical command name present in ``_LAZY_SUBCOMMANDS``.

    Returns:
        The imported Click command object.
    """
    module_name, attr_name = _LAZY_SUBCOMMANDS[canonical].rsplit(".", 1)
    return getattr(importlib.import_module(module_name), attr_name)


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
def test_lazy_import_path_resolves_to_a_click_command(canonical: str) -> None:
    """Every import path resolves to a Click command with the expected name.

    Args:
        canonical: Canonical command name under test.
    """
    command = _load_command(canonical)
    assert_that(isinstance(command, click.Command)).is_true()
    assert_that(command.name).is_equal_to(canonical)


@pytest.mark.parametrize("canonical", _CANONICAL_COMMANDS)
def test_static_short_help_matches_the_command(canonical: str) -> None:
    """The static help table matches what Click would render at runtime.

    Args:
        canonical: Canonical command name under test.
    """
    command = _load_command(canonical)
    assert_that(_COMMAND_SHORT_HELP[canonical]).is_equal_to(
        command.get_short_help_str(limit=SHORT_HELP_LIMIT),
    )


def test_short_help_entries_fit_the_render_limit() -> None:
    """No table entry is long enough to be truncated when rendered."""
    for canonical, short_help in _COMMAND_SHORT_HELP.items():
        assert_that(len(short_help)).described_as(canonical).is_less_than_or_equal_to(
            SHORT_HELP_LIMIT,
        )
