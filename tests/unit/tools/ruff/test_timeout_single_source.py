"""Tests that ruff has a single source of truth for its default timeout.

Regression coverage for #1229: ``RUFF_DEFAULT_TIMEOUT`` was independently
redefined in three modules. It must now be defined once in the ruff definition
and re-exported from the execution helpers.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.tools.definitions.ruff import RUFF_DEFAULT_TIMEOUT as DEFINITION_TIMEOUT
from lintro.tools.implementations.ruff.check import (
    RUFF_DEFAULT_TIMEOUT as CHECK_TIMEOUT,
)
from lintro.tools.implementations.ruff.fix import RUFF_DEFAULT_TIMEOUT as FIX_TIMEOUT


def test_ruff_timeout_single_source_check() -> None:
    """The check module re-exports the definition constant (import identity)."""
    assert_that(CHECK_TIMEOUT).is_equal_to(DEFINITION_TIMEOUT)
    assert_that(CHECK_TIMEOUT is DEFINITION_TIMEOUT).is_true()


def test_ruff_timeout_single_source_fix() -> None:
    """The fix module re-exports the definition constant (import identity)."""
    assert_that(FIX_TIMEOUT).is_equal_to(DEFINITION_TIMEOUT)
    assert_that(FIX_TIMEOUT is DEFINITION_TIMEOUT).is_true()


def test_ruff_check_routes_through_prepare_execution(
    mock_ruff_tool: MagicMock,
) -> None:
    """``execute_ruff_check`` delegates preparation to ``_prepare_execution``.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    from lintro.tools.implementations.ruff.check import execute_ruff_check

    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(True, "[]"),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        execute_ruff_check(mock_ruff_tool, ["/test/project"])

    mock_ruff_tool._prepare_execution.assert_called_once()


def test_ruff_fix_routes_through_prepare_execution(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_empty_output: str,
) -> None:
    """``execute_ruff_fix`` delegates preparation to ``_prepare_execution``.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    from lintro.tools.implementations.ruff.fix import execute_ruff_fix

    mock_ruff_tool._run_subprocess.side_effect = [
        (True, sample_ruff_json_empty_output),
        (True, sample_ruff_json_empty_output),
    ]

    execute_ruff_fix(mock_ruff_tool, ["/test/project"])

    mock_ruff_tool._prepare_execution.assert_called_once()


def test_ruff_plugin_check_and_fix_invoke_prepare_execution(
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """The real plugin's ``check``/``fix`` route ruff through ``_prepare_execution``.

    Args:
        ruff_execution_context: Factory for mock execution contexts.
    """
    import os

    with patch.dict(os.environ, {"LINTRO_TEST_MODE": "1"}):
        from lintro.tools.definitions.ruff import RuffPlugin

        plugin = RuffPlugin()

    with (
        patch.object(
            plugin,
            "_prepare_execution",
            return_value=ruff_execution_context(),
        ) as mock_prepare,
        patch.object(plugin, "_run_subprocess", return_value=(True, "[]")),
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(True, "[]"),
        ),
    ):
        plugin.check(["/test/project"], {})
        plugin.fix(["/test/project"], {})

    assert_that(mock_prepare.call_count).is_equal_to(2)
