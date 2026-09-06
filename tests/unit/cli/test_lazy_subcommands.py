"""Guards on the lazy subcommand tables in :mod:`lintro.cli` (#1305)."""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import click
import pytest
from assertpy import assert_that

import lintro.cli as cli_module
from lintro.cli import (
    _COMMAND_ALIASES,
    _COMMAND_ATTRIBUTES,
    _COMMAND_MODULES,
    cli,
)


@pytest.fixture(autouse=True)
def isolated_group() -> Iterator[None]:
    """Restore the module-level group's command cache after each test.

    ``get_command`` caches resolved commands on the singleton, so without this
    one test's lookups would satisfy another test's assertions.

    Yields:
        None: Control returns to the test with an empty command cache.
    """
    saved = dict(cli.commands)
    cli.commands.clear()
    try:
        yield
    finally:
        cli.commands.clear()
        cli.commands.update(saved)


@pytest.fixture
def context() -> click.Context:
    """Return a throwaway Click context bound to the lintro group.

    Returns:
        click.Context: Context usable for command lookups.
    """
    return click.Context(cli)


@pytest.mark.parametrize("canonical", sorted(_COMMAND_MODULES))
def test_every_canonical_command_resolves(
    canonical: str,
    context: click.Context,
) -> None:
    """Each table entry names a real module attribute holding a Click command.

    Args:
        canonical: Canonical command name under test.
        context: Click context fixture.
    """
    module_path, attribute = _COMMAND_MODULES[canonical]
    # Module path comes from lintro's own static table.
    module = importlib.import_module(module_path)  # nosemgrep: non-literal-import
    expected = getattr(module, attribute)

    resolved = cli.get_command(context, canonical)

    assert_that(resolved).is_same_as(expected)
    assert_that(resolved).is_instance_of(click.Command)


@pytest.mark.parametrize("alias", sorted(_COMMAND_ALIASES))
def test_every_alias_resolves_to_its_canonical_command(
    alias: str,
    context: click.Context,
) -> None:
    """Aliases resolve to the same object as their canonical command.

    Args:
        alias: Alias name under test.
        context: Click context fixture.
    """
    canonical = _COMMAND_ALIASES[alias]

    assert_that(_COMMAND_MODULES).contains_key(canonical)
    assert_that(cli.get_command(context, alias)).is_same_as(
        cli.get_command(context, canonical),
    )


def test_resolved_commands_carry_their_canonical_name(
    context: click.Context,
) -> None:
    """Help rendering groups aliases by ``_canonical_name``.

    Args:
        context: Click context fixture.
    """
    command = cli.get_command(context, "chk")

    assert_that(getattr(command, "_canonical_name", None)).is_equal_to("check")


def test_list_commands_covers_canonical_names_and_aliases(
    context: click.Context,
) -> None:
    """``list_commands`` advertises everything without importing anything.

    Args:
        context: Click context fixture.
    """
    names = cli.list_commands(context)

    assert_that(names).is_equal_to(sorted(names))
    assert_that(set(names)).contains(*_COMMAND_MODULES)
    assert_that(set(names)).contains(*_COMMAND_ALIASES)


def test_unknown_command_resolves_to_none(context: click.Context) -> None:
    """An unknown name is not silently turned into a command.

    Args:
        context: Click context fixture.
    """
    assert_that(cli.get_command(context, "definitely-not-a-command")).is_none()


def test_load_all_commands_populates_the_group(context: click.Context) -> None:
    """``load_all_commands`` materializes the tree for ``Group.commands`` readers.

    Args:
        context: Click context fixture.
    """
    assert_that(cli.commands).is_empty()

    cli.load_all_commands(context)

    assert_that(set(cli.commands)).contains(*_COMMAND_MODULES)
    assert_that(set(cli.commands)).contains(*_COMMAND_ALIASES)


def test_a_broken_table_entry_surfaces_as_an_import_error(
    context: click.Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad table entry fails loudly instead of resolving to ``None``.

    Args:
        context: Click context fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setitem(
        _COMMAND_MODULES,
        "check",
        ("lintro.cli_utils.commands.definitely_not_a_module", "check_command"),
    )

    with pytest.raises(ModuleNotFoundError):
        cli.get_command(context, "check")


@pytest.mark.parametrize("attribute", sorted(_COMMAND_ATTRIBUTES))
def test_command_objects_stay_importable_from_the_cli_module(
    attribute: str,
    context: click.Context,
) -> None:
    """The names this module used to export eagerly still resolve.

    Lazy loading must not silently break
    ``from lintro.cli import check_command`` for out-of-tree callers.

    Args:
        attribute: Historical module attribute under test.
        context: Click context fixture.
    """
    resolved = getattr(cli_module, attribute)

    assert_that(resolved).is_instance_of(click.Command)
    assert_that(resolved).is_same_as(
        cli.get_command(context, _COMMAND_ATTRIBUTES[attribute]),
    )


def test_unknown_module_attribute_still_raises() -> None:
    """The module ``__getattr__`` does not swallow genuine typos."""
    with pytest.raises(AttributeError):
        getattr(cli_module, "definitely_not_an_attribute")  # noqa: B009


def test_a_table_entry_that_is_not_a_command_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A table entry pointing at a non-command fails with a clear error.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setitem(
        _COMMAND_MODULES,
        "check",
        ("lintro.cli_utils.commands.check", "__doc__"),
    )

    with pytest.raises(TypeError, match="not a click.Command"):
        cli_module._load_command("check")
