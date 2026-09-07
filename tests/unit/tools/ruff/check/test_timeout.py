"""Tests for timeout configuration in execute_ruff_check."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.tools.ruff.check import (
    RUFF_DEFAULT_TIMEOUT,
    execute_ruff_check,
)


def test_execute_ruff_check_uses_default_timeout() -> None:
    """Verify default timeout constant is set correctly."""
    assert_that(RUFF_DEFAULT_TIMEOUT).is_equal_to(30)


def test_execute_ruff_check_uses_context_timeout(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Use the timeout resolved by the shared preparation pipeline.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    mock_ruff_tool.prepare.return_value = ruff_execution_context(
        timeout=60,
    )
    timeouts: list[int] = []

    def fake_run(**kwargs: object) -> tuple[bool, str]:
        """Record the timeout ruff was given and report a clean run.

        Args:
            **kwargs: Arguments the caller passed to the runner.

        Returns:
            A successful run with empty JSON findings.
        """
        timeouts.append(cast("int", kwargs["timeout"]))
        return (True, "[]")

    with (
        patch(
            "lintro.tools.ruff.check.run_subprocess_with_timeout",
            side_effect=fake_run,
        ),
        patch(
            "lintro.tools.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

    assert_that(timeouts).is_equal_to([60])
    assert_that(result.success).is_true()
