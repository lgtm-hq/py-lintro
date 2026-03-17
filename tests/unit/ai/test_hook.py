"""Tests for AI post-execution hook."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.hook import AIPostExecutionHook
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import MockIssue

# ---------------------------------------------------------------------------
# TestShouldRun
# ---------------------------------------------------------------------------


def test_should_run_returns_true_for_check_when_enabled():
    """Verify should_run returns True for CHECK action when AI is enabled."""
    config = LintroConfig(ai=AIConfig(enabled=True))
    hook = AIPostExecutionHook(config)

    result = hook.should_run(Action.CHECK)

    assert_that(result).is_true()


def test_should_run_returns_true_for_fix_when_enabled():
    """Verify should_run returns True for FIX action when AI is enabled."""
    config = LintroConfig(ai=AIConfig(enabled=True))
    hook = AIPostExecutionHook(config)

    result = hook.should_run(Action.FIX)

    assert_that(result).is_true()


def test_should_run_returns_false_for_test_action():
    """Verify should_run returns False for TEST action even when AI is enabled."""
    config = LintroConfig(ai=AIConfig(enabled=True))
    hook = AIPostExecutionHook(config)

    result = hook.should_run(Action.TEST)

    assert_that(result).is_false()


def test_should_run_returns_false_when_disabled():
    """Verify should_run returns False when AI is disabled."""
    config = LintroConfig(ai=AIConfig(enabled=False))
    hook = AIPostExecutionHook(config)

    result = hook.should_run(Action.CHECK)

    assert_that(result).is_false()


# ---------------------------------------------------------------------------
# TestExecute
# ---------------------------------------------------------------------------


@patch("lintro.ai.orchestrator.run_ai_enhancement")
def test_execute_calls_run_ai_enhancement(mock_run_ai_enhancement):
    """Verify execute delegates to run_ai_enhancement with correct arguments."""
    config = LintroConfig(ai=AIConfig(enabled=True))
    hook = AIPostExecutionHook(config, ai_fix=True)
    console_logger = MagicMock()
    results = [
        ToolResult(
            name="ruff",
            success=False,
            issues_count=1,
            issues=[
                MockIssue(
                    file="src/main.py",
                    line=1,
                    message="Use of assert",
                    code="B101",
                ),
            ],
        ),
    ]

    hook.execute(
        action=Action.CHECK,
        all_results=results,
        console_logger=console_logger,
        output_format="json",
    )

    mock_run_ai_enhancement.assert_called_once_with(
        action=Action.CHECK,
        all_results=results,
        lintro_config=config,
        logger=console_logger,
        output_format="json",
        ai_fix=True,
    )


@patch("lintro.ai.orchestrator.run_ai_enhancement")
def test_execute_catches_exceptions_and_logs_warning(mock_run_ai_enhancement):
    """Exceptions don't propagate; warning is logged."""
    mock_run_ai_enhancement.side_effect = RuntimeError("provider exploded")
    config = LintroConfig(ai=AIConfig(enabled=True))
    hook = AIPostExecutionHook(config)
    console_logger = MagicMock()
    results = [
        ToolResult(
            name="ruff",
            success=False,
            issues_count=1,
            issues=[
                MockIssue(
                    file="src/main.py",
                    line=1,
                    message="err",
                    code="E501",
                ),
            ],
        ),
    ]

    hook.execute(
        action=Action.CHECK,
        all_results=results,
        console_logger=console_logger,
        output_format="terminal",
    )

    console_logger.warning.assert_called_once()
    warning_msg = console_logger.warning.call_args[0][0]
    assert_that(warning_msg).contains("provider exploded")


def test_execute_handles_import_failure():
    """Verify graceful handling when the lazy import of run_ai_enhancement fails."""
    config = LintroConfig(ai=AIConfig(enabled=True))
    hook = AIPostExecutionHook(config)
    console_logger = MagicMock()
    results = [
        ToolResult(
            name="ruff",
            success=False,
            issues_count=1,
            issues=[
                MockIssue(
                    file="src/main.py",
                    line=1,
                    message="err",
                    code="E501",
                ),
            ],
        ),
    ]

    with patch.dict(
        "sys.modules",
        {"lintro.ai.orchestrator": None},
    ):
        hook.execute(
            action=Action.CHECK,
            all_results=results,
            console_logger=console_logger,
            output_format="terminal",
        )

    console_logger.warning.assert_called_once()
    warning_msg = console_logger.warning.call_args[0][0]
    assert_that(warning_msg).contains("AI enhancement unavailable")
