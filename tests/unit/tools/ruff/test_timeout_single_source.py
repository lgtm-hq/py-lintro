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

    Raises:
        TypeError: If a recorded ``cmd`` is not a list, which would otherwise
            be coerced silently into a list of characters.
    """
    from lintro.tools.implementations.ruff.check import execute_ruff_check

    # The shared fixture leaves format_check False, but RuffPlugin's defaults
    # set it True, so production runs both `ruff check` and `ruff format
    # --check`. Opt in here or the second subprocess is never covered (#2315).
    mock_ruff_tool.options["format_check"] = True
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
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_format_check_output",
            return_value=[],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

    # Both the lint and the format --check subprocess must honour the context.
    assert_that(observed).is_length(2)
    argvs: list[str] = []
    for call in observed:
        cmd = call["cmd"]
        if not isinstance(cmd, list):
            raise TypeError(f"expected a cmd list, got {type(cmd).__name__}")
        argvs.append(" ".join(str(part) for part in cmd))
    assert_that(argvs[0]).contains("check")
    assert_that(argvs[1]).contains("format")
    assert_that(argvs[1]).contains("--check")
    assert_that([call["timeout"] for call in observed]).is_equal_to([77, 77])
    assert_that([call["cwd"] for call in observed]).is_equal_to(
        ["/prepared/cwd", "/prepared/cwd"],
    )
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

    Raises:
        TypeError: If a recorded ``cmd`` is not a list, which would otherwise
            be coerced silently into a list of characters.
    """
    from lintro.tools.implementations.ruff.fix import execute_ruff_fix

    # The shared fixture carries no ``format`` key, but RuffPlugin's defaults
    # set it True, so production also runs `ruff format --check` and `ruff
    # format`. Opt in here or two of the four subprocesses are never covered
    # (#2315).
    mock_ruff_tool.options["format"] = True
    mock_ruff_tool.prepare.return_value = ruff_execution_context(
        timeout=91,
    )
    observed: list[dict[str, object]] = []

    def fake_run(**kwargs: object) -> tuple[bool, str]:
        """Record the subprocess arguments and report a clean ruff run.

        Args:
            **kwargs: Arguments ruff passed to ``_run_subprocess``.

        Returns:
            A successful run with empty JSON findings.
        """
        observed.append(kwargs)
        return (True, sample_ruff_json_empty_output)

    mock_ruff_tool._run_subprocess.side_effect = fake_run

    result = execute_ruff_fix(mock_ruff_tool, ["/test/project"])

    # Lint check, format --check, lint --fix, format apply: all four honour
    # the one prepared timeout.
    assert_that([call["timeout"] for call in observed]).is_equal_to(
        [91, 91, 91, 91],
    )
    argvs: list[str] = []
    for call in observed:
        cmd = call["cmd"]
        if not isinstance(cmd, list):
            raise TypeError(f"expected a cmd list, got {type(cmd).__name__}")
        argvs.append(" ".join(str(part) for part in cmd))
    assert_that(argvs[1]).contains("format")
    assert_that(argvs[1]).contains("--check")
    assert_that(argvs[2]).contains("--fix")
    assert_that(argvs[3]).contains("format")
    assert_that(argvs[3]).does_not_contain("--check")
    assert_that(result.success).is_true()


def test_ruff_plugin_check_and_fix_invoke_prepare(
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Both plugin entry points carry the prepared timeout into the runner.

    The timeout is a subprocess-runner keyword argument, not a ruff CLI flag,
    so this asserts on the call kwargs rather than on argv (#2315).

    Args:
        ruff_execution_context: Factory for mock execution contexts.
    """
    import os

    with patch.dict(os.environ, {"LINTRO_TEST_MODE": "1"}):
        from lintro.tools.definitions.ruff import RuffPlugin

        plugin = RuffPlugin()

    prepared: list[dict[str, object]] = []
    check_timeouts: list[object] = []
    fix_timeouts: list[object] = []

    def fake_prepare(**kwargs: object) -> MagicMock:
        """Record one preparation and hand back a fixed context.

        ``_prepare_execution`` takes no ``action`` parameter, so the whole
        kwargs mapping is recorded rather than a field that never exists.

        Args:
            **kwargs: Arguments the plugin passed to ``prepare``.

        Returns:
            A context pinning the timeout the subprocess calls must honour.
        """
        prepared.append(dict(kwargs))
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
