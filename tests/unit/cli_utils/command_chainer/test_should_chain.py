"""Tests for CommandChainer.should_chain method."""

from __future__ import annotations

import click
from assertpy import assert_that

from lintro.cli_utils.command_chainer import CommandChainer


def test_should_chain_with_comma_separator(mock_group: click.Group) -> None:
    """Test detection of comma separator in args.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)

    assert_that(chainer.should_chain(["fmt", ",", "chk"])).is_true()


def test_should_chain_with_joined_commands(mock_group: click.Group) -> None:
    """Test detection of joined command names.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)

    assert_that(chainer.should_chain(["fmt,chk"])).is_true()


def test_should_not_chain_single_command(mock_group: click.Group) -> None:
    """Test that single command does not trigger chaining.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)

    assert_that(chainer.should_chain(["fmt", "."])).is_false()


def test_should_not_chain_comma_in_argument(mock_group: click.Group) -> None:
    """Test that comma in non-command argument doesn't trigger chaining.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)

    assert_that(chainer.should_chain(["fmt", "--tools", "ruff,bandit"])).is_false()


def test_should_not_chain_known_commands_in_option_value() -> None:
    """Known command names in an option value are not a command chain."""
    from lintro.cli import cli

    chainer = CommandChainer(cli)

    assert_that(
        chainer.should_chain(["check", "--exclude", "w,test"]),
    ).is_false()
    assert_that(chainer.command_names).contains("w")


def test_should_not_chain_empty_args(mock_group: click.Group) -> None:
    """Test that empty args do not trigger chaining.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)

    assert_that(chainer.should_chain([])).is_false()
