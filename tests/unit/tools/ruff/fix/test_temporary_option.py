"""Tests for _temporary_option context manager."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.tools.implementations.ruff.fix import _temporary_option


def test_temporary_option_sets_and_restores_value(
    mock_ruff_tool: MagicMock,
) -> None:
    """Verify temporary option is set within context and restored after.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    mock_ruff_tool.options = {"existing_key": "original_value"}

    with _temporary_option(mock_ruff_tool, "new_key", "temp_value"):
        assert_that(mock_ruff_tool.options["new_key"]).is_equal_to("temp_value")

    assert_that("new_key" in mock_ruff_tool.options).is_false()


def test_temporary_option_restores_original_value(
    mock_ruff_tool: MagicMock,
) -> None:
    """Verify original value is restored after context exits.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    mock_ruff_tool.options = {"key": "original"}

    with _temporary_option(mock_ruff_tool, "key", "temporary"):
        assert_that(mock_ruff_tool.options["key"]).is_equal_to("temporary")

    assert_that(mock_ruff_tool.options["key"]).is_equal_to("original")


def test_temporary_option_restores_on_exception(
    mock_ruff_tool: MagicMock,
) -> None:
    """Verify option is restored even when exception occurs in context.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.

    Raises:
        ValueError: Test exception raised within the context.
    """
    mock_ruff_tool.options = {"key": "original"}

    with (
        pytest.raises(ValueError),
        _temporary_option(mock_ruff_tool, "key", "temporary"),
    ):
        assert_that(mock_ruff_tool.options["key"]).is_equal_to(
            "temporary",
        )
        raise ValueError("Test exception")

    assert_that(mock_ruff_tool.options["key"]).is_equal_to("original")


def test_temporary_option_removes_new_key_on_exit(
    mock_ruff_tool: MagicMock,
) -> None:
    """Verify new key is removed if it didn't exist before.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    mock_ruff_tool.options = {}

    with _temporary_option(mock_ruff_tool, "new_key", True):
        assert_that(mock_ruff_tool.options["new_key"]).is_true()

    assert_that("new_key" in mock_ruff_tool.options).is_false()
