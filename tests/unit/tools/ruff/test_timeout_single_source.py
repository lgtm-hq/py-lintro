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
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """The prepared context's timeout and cwd reach ruff's subprocess call.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    from lintro.tools.implementations.ruff.check import execute_ruff_check

    mock_ruff_tool.prepare.return_value = ruff_execution_context(
        timeout=77,
        cwd="/prepared/cwd",
    )
    observed: list[dict[str, object]] = []

    def fake_run(**kwargs: object) -> tuple[bool, str]:
        """Record the subprocess arguments and report a clean run.

        Args:
            **kwargs: Arguments ruff passed to the timeout-aware runner.

        Returns:
            A successful run with empty JSON findings.
        """
        observed.append(kwargs)
        return (True, "[]")

    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            side_effect=fake_run,
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

    assert_that(observed).is_length(1)
    assert_that(observed[0]["timeout"]).is_equal_to(77)
    assert_that(observed[0]["cwd"]).is_equal_to("/prepared/cwd")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_ruff_fix_routes_through_prepare_execution(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
    sample_ruff_json_empty_output: str,
) -> None:
    """The prepared context's timeout reaches every ruff fix subprocess call.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    from lintro.tools.implementations.ruff.fix import execute_ruff_fix

    mock_ruff_tool.prepare.return_value = ruff_execution_context(
        timeout=91,
    )
    timeouts: list[object] = []

    def fake_run(**kwargs: object) -> tuple[bool, str]:
        """Record the timeout and report a clean ruff run.

        Args:
            **kwargs: Arguments ruff passed to ``_run_subprocess``.

        Returns:
            A successful run with empty JSON findings.
        """
        timeouts.append(kwargs["timeout"])
        return (True, sample_ruff_json_empty_output)

    mock_ruff_tool._run_subprocess.side_effect = fake_run

    result = execute_ruff_fix(mock_ruff_tool, ["/test/project"])

    assert_that(timeouts).is_not_empty()
    assert_that(set(timeouts)).is_equal_to({91})
    assert_that(result.success).is_true()


def test_ruff_plugin_check_and_fix_invoke_prepare(
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Both plugin entry points carry the prepared timeout into ruff's argv.

    Args:
        ruff_execution_context: Factory for mock execution contexts.
    """
    import os

    with patch.dict(os.environ, {"LINTRO_TEST_MODE": "1"}):
        from lintro.tools.definitions.ruff import RuffPlugin

        plugin = RuffPlugin()

    prepared: list[str] = []
    check_timeouts: list[object] = []
    fix_timeouts: list[object] = []

    def fake_prepare(**kwargs: object) -> MagicMock:
        """Record the action being prepared and hand back a fixed context.

        Args:
            **kwargs: Arguments the plugin passed to ``prepare``.

        Returns:
            A context pinning the timeout the subprocess calls must honour.
        """
        prepared.append(str(kwargs.get("action", "")))
        return ruff_execution_context(timeout=64)

    def fake_check_run(**kwargs: object) -> tuple[bool, str]:
        """Record the check-path timeout and report a clean run.

        Args:
            **kwargs: Arguments ruff passed to the timeout-aware runner.

        Returns:
            A successful run with empty JSON findings.
        """
        check_timeouts.append(kwargs["timeout"])
        return (True, "[]")

    def fake_fix_run(**kwargs: object) -> tuple[bool, str]:
        """Record the fix-path timeout and report a clean run.

        Args:
            **kwargs: Arguments ruff passed to ``_run_subprocess``.

        Returns:
            A successful run with empty JSON findings.
        """
        fix_timeouts.append(kwargs["timeout"])
        return (True, "[]")

    with (
        patch.object(plugin, "prepare", side_effect=fake_prepare),
        patch.object(plugin, "_run_subprocess", side_effect=fake_fix_run),
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            side_effect=fake_check_run,
        ),
    ):
        check_result = plugin.check(["/test/project"], {})
        fix_result = plugin.fix(["/test/project"], {})

    assert_that(prepared).is_length(2)
    assert_that(set(check_timeouts)).is_equal_to({64})
    assert_that(set(fix_timeouts)).is_equal_to({64})
    assert_that(check_result.success).is_true()
    assert_that(fix_result.success).is_true()
