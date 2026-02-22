"""Tests for CommandChainer execute_chain and _execute_single_command methods."""

from __future__ import annotations

from unittest.mock import patch

import click
import pytest
from assertpy import assert_that

from lintro.cli_utils.command_chainer import CommandChainer


def test_execute_chain_all_success(mock_group: click.Group) -> None:
    """Test execution of chain where all commands succeed.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)
    ctx = click.Context(mock_group)

    with patch.object(
        chainer,
        "_execute_single_command",
        return_value=0,
    ) as mock_exec:
        result = chainer.execute_chain(ctx, [["fmt", "."], ["chk", "."]])

        assert_that(result).is_equal_to(0)
        assert_that(mock_exec.call_count).is_equal_to(2)


def test_execute_chain_returns_max_exit_code(mock_group: click.Group) -> None:
    """Test that execute_chain returns the maximum exit code.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)
    ctx = click.Context(mock_group)

    with patch.object(
        chainer,
        "_execute_single_command",
        side_effect=[0, 2, 1],
    ):
        result = chainer.execute_chain(
            ctx,
            [["fmt", "."], ["chk", "."], ["tst"]],
        )

        assert_that(result).is_equal_to(2)


def test_execute_chain_empty_groups(mock_group: click.Group) -> None:
    """Test execution with empty command groups.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)
    ctx = click.Context(mock_group)

    result = chainer.execute_chain(ctx, [])

    assert_that(result).is_equal_to(0)


def test_execute_chain_skips_empty_groups(mock_group: click.Group) -> None:
    """Test that empty groups within the list are skipped.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)
    ctx = click.Context(mock_group)

    with patch.object(
        chainer,
        "_execute_single_command",
        return_value=0,
    ) as mock_exec:
        result = chainer.execute_chain(ctx, [["fmt"], [], ["chk"]])

        assert_that(result).is_equal_to(0)
        assert_that(mock_exec.call_count).is_equal_to(2)


def test_execute_single_command_success(mock_group: click.Group) -> None:
    """Test successful single command execution.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)
    parent_ctx = click.Context(mock_group, info_name="lintro")

    result = chainer._execute_single_command(parent_ctx, ["fmt", "."])

    assert_that(result).is_equal_to(0)


def test_execute_single_command_handles_system_exit(mock_group: click.Group) -> None:
    """Test handling of SystemExit from command.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)
    parent_ctx = click.Context(mock_group, info_name="lintro")

    with patch.object(
        mock_group,
        "invoke",
        side_effect=SystemExit(2),
    ):
        result = chainer._execute_single_command(parent_ctx, ["fmt"])

        assert_that(result).is_equal_to(2)


def test_execute_single_command_handles_system_exit_none(
    mock_group: click.Group,
) -> None:
    """Test handling of SystemExit with None code.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)
    parent_ctx = click.Context(mock_group, info_name="lintro")

    with patch.object(
        mock_group,
        "invoke",
        side_effect=SystemExit(None),
    ):
        result = chainer._execute_single_command(parent_ctx, ["fmt"])

        assert_that(result).is_equal_to(0)


def test_execute_single_command_reraises_keyboard_interrupt(
    mock_group: click.Group,
) -> None:
    """Test that KeyboardInterrupt is re-raised.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)
    parent_ctx = click.Context(mock_group, info_name="lintro")

    with (
        patch.object(
            mock_group,
            "invoke",
            side_effect=KeyboardInterrupt(),
        ),
        pytest.raises(KeyboardInterrupt),
    ):
        chainer._execute_single_command(parent_ctx, ["fmt"])


def test_execute_single_command_handles_exception(mock_group: click.Group) -> None:
    """Test handling of generic exceptions.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)
    parent_ctx = click.Context(mock_group, info_name="lintro")

    with patch.object(
        mock_group,
        "make_context",
        side_effect=RuntimeError("test error"),
    ):
        result = chainer._execute_single_command(parent_ctx, ["fmt"])

        assert_that(result).is_equal_to(1)


def test_execute_single_command_uses_exit_code_attribute(
    mock_group: click.Group,
) -> None:
    """Test that exit_code attribute from exception is used.

    Args:
        mock_group: Mocked Click group fixture.
    """
    chainer = CommandChainer(mock_group)
    parent_ctx = click.Context(mock_group, info_name="lintro")

    class CustomError(Exception):
        """Custom error with exit_code attribute."""

        exit_code = 42

    with patch.object(
        mock_group,
        "make_context",
        side_effect=CustomError("custom error"),
    ):
        result = chainer._execute_single_command(parent_ctx, ["fmt"])

        assert_that(result).is_equal_to(42)
