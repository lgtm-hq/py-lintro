"""Tests for AI post-execution hook."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.effective_config import resolve_effective_ai_config
from lintro.ai.enums import AITransport
from lintro.ai.hook import AIPostExecutionHook
from lintro.ai.models import AIResult
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import MockIssue, RecordingConsoleLogger

# ---------------------------------------------------------------------------
# TestShouldRun
# ---------------------------------------------------------------------------


def test_should_run_returns_true_for_check_when_enabled():
    """Verify should_run returns True for CHECK action when AI is enabled."""
    config = LintroConfig(
        ai=AIConfig(enabled=True, transport=AITransport.API).model_dump(),
    )
    hook = AIPostExecutionHook(config)

    result = hook.should_run(Action.CHECK)

    assert_that(result).is_true()


def test_should_run_returns_true_for_fix_when_enabled():
    """Verify should_run returns True for FIX action when AI is enabled."""
    config = LintroConfig(
        ai=AIConfig(enabled=True, transport=AITransport.API).model_dump(),
    )
    hook = AIPostExecutionHook(config)

    result = hook.should_run(Action.FIX)

    assert_that(result).is_true()


def test_should_run_returns_false_for_test_action():
    """Verify should_run returns False for TEST action even when AI is enabled."""
    config = LintroConfig(
        ai=AIConfig(enabled=True, transport=AITransport.API).model_dump(),
    )
    hook = AIPostExecutionHook(config)

    result = hook.should_run(Action.TEST)

    assert_that(result).is_false()


def test_should_run_returns_false_when_disabled():
    """Verify should_run returns False when AI is disabled."""
    config = LintroConfig(ai=AIConfig(enabled=False).model_dump())
    hook = AIPostExecutionHook(config)

    result = hook.should_run(Action.CHECK)

    assert_that(result).is_false()


def test_should_run_true_when_lint_enabled():
    """Lint summarization runs when ai.lint is enabled."""
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            lint=True,
            review=False,
            transport=AITransport.API,
        ).model_dump(),
    )
    hook = AIPostExecutionHook(config)

    assert_that(hook.should_run(Action.CHECK)).is_true()


def test_should_run_false_when_only_review_enabled():
    """Review-only config does not trigger the lint-summary hook."""
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            lint=False,
            review=True,
            transport=AITransport.API,
        ).model_dump(),
    )
    hook = AIPostExecutionHook(config)

    assert_that(hook.should_run(Action.CHECK)).is_false()
    assert_that(hook.should_run(Action.FIX)).is_false()


# ---------------------------------------------------------------------------
# TestExecute
# ---------------------------------------------------------------------------


def test_execute_calls_run_ai_enhancement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Execute forwards its inputs to run_ai_enhancement and returns its result.

    Args:
        monkeypatch: Pytest monkeypatch fixture, used to install a recording
            stand-in for ``run_ai_enhancement``.
    """
    config = LintroConfig(
        ai=AIConfig(enabled=True, transport=AITransport.API).model_dump(),
    )
    hook = AIPostExecutionHook(config, ai_fix=True)
    console_logger = RecordingConsoleLogger()
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
    forwarded: list[dict[str, Any]] = []
    expected = AIResult(fixes_applied=3)

    def _record(**kwargs: Any) -> AIResult:
        """Record the enhancement call and return a recognisable result.

        Args:
            **kwargs: Keyword arguments the hook forwards.

        Returns:
            AIResult: The sentinel result the hook must hand back.
        """
        forwarded.append(kwargs)
        return expected

    monkeypatch.setattr("lintro.ai.orchestrator.run_ai_enhancement", _record)

    outcome = hook.execute(
        action=Action.CHECK,
        all_results=results,
        console_logger=console_logger,
        output_format="json",
    )

    assert_that(outcome).is_same_as(expected)
    assert_that(forwarded).is_length(1)
    assert_that(forwarded[0]).is_equal_to(
        {
            "action": Action.CHECK,
            "all_results": results,
            "lintro_config": config,
            "ai_config": resolve_effective_ai_config(config.ai),
            "logger": console_logger,
            "output_format": "json",
            "ai_fix": True,
        },
    )


@patch("lintro.ai.orchestrator.run_ai_enhancement")
def test_execute_catches_exceptions_and_logs_warning(
    mock_run_ai_enhancement: MagicMock,
) -> None:
    """A provider failure warns and returns an errored result, never raises.

    Args:
        mock_run_ai_enhancement: Patched orchestrator entry point.
    """
    mock_run_ai_enhancement.side_effect = RuntimeError("provider exploded")
    config = LintroConfig(
        ai=AIConfig(enabled=True, transport=AITransport.API).model_dump(),
    )
    hook = AIPostExecutionHook(config)
    console_logger = RecordingConsoleLogger()
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

    result = hook.execute(
        action=Action.CHECK,
        all_results=results,
        console_logger=console_logger,
        output_format="terminal",
    )

    # The warning alone does not prove the failure was reported: a regression
    # that logged and then returned a plain AIResult() would look identical.
    assert_that(result.error).is_true()
    assert_that(console_logger.warnings).is_length(1)
    assert_that(console_logger.warnings[0]).contains("provider exploded")


def test_execute_handles_import_failure() -> None:
    """A failed lazy import warns and returns an errored result."""
    config = LintroConfig(
        ai=AIConfig(enabled=True, transport=AITransport.API).model_dump(),
    )
    hook = AIPostExecutionHook(config)
    console_logger = RecordingConsoleLogger()
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
        result = hook.execute(
            action=Action.CHECK,
            all_results=results,
            console_logger=console_logger,
            output_format="terminal",
        )

    assert_that(result.error).is_true()
    assert_that(console_logger.warnings).is_length(1)
    assert_that(console_logger.warnings[0]).contains("AI enhancement unavailable")


@patch("lintro.ai.orchestrator.run_ai_enhancement")
def test_execute_reraises_when_fail_on_ai_error_is_set(
    mock_run_ai_enhancement: MagicMock,
) -> None:
    """``fail_on_ai_error`` propagates the original exception instead of warning.

    Args:
        mock_run_ai_enhancement: Patched orchestrator entry point.
    """
    mock_run_ai_enhancement.side_effect = RuntimeError("provider exploded")
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            transport=AITransport.API,
            fail_on_ai_error=True,
        ).model_dump(),
    )
    hook = AIPostExecutionHook(config)
    console_logger = RecordingConsoleLogger()
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

    with pytest.raises(RuntimeError, match="provider exploded"):
        hook.execute(
            action=Action.CHECK,
            all_results=results,
            console_logger=console_logger,
            output_format="terminal",
        )

    # The raising path returns before the warning, so nothing was printed.
    assert_that(console_logger.warnings).is_empty()
