"""Tests for AI orchestrator check action and summary attachment."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.models import AIFixSuggestion, AISummary
from lintro.ai.orchestrator import (
    _log_fix_limit_message,
    run_ai_enhancement,
)
from lintro.ai.providers.base import AIResponse
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import MockAIProvider, MockIssue

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def single_issue_result():
    """ToolResult with one ruff issue."""
    return ToolResult(
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
    )


@pytest.fixture
def check_config():
    """LintroConfig with AI enabled and max_fix_issues=5."""
    return LintroConfig(ai=AIConfig(enabled=True, max_fix_issues=5))


@pytest.fixture
def mock_logger():
    """MagicMock logger."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Check action with fix metadata
# ---------------------------------------------------------------------------


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.orchestrator.generate_summary")
@patch("lintro.ai.pipeline.generate_fixes")
def test_run_ai_enhancement_check_fix_preserves_summary_and_fix_metadata(
    mock_generate_fixes,
    mock_generate_summary,
    mock_get_provider,
    _mock_require_ai,
    single_issue_result,
    check_config,
    mock_logger,
):
    """Verify check+fix action attaches both summary and fix metadata to the result."""
    result = single_issue_result
    config = check_config
    logger = mock_logger

    mock_get_provider.return_value = MockAIProvider()
    mock_generate_summary.return_value = AISummary(overview="AI overview")
    mock_generate_fixes.return_value = [
        AIFixSuggestion(
            file="src/main.py",
            line=1,
            code="B101",
            explanation="Replace assert",
        ),
    ]

    run_ai_enhancement(
        action=Action.CHECK,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
        ai_fix=True,
    )

    assert_that(result.ai_metadata).is_not_none()
    assert_that(result.ai_metadata).contains_key("summary")
    assert_that(result.ai_metadata).contains_key("fix_suggestions")
    assert_that(result.ai_metadata["summary"]["overview"]).is_equal_to(
        "AI overview",
    )
    assert_that(result.ai_metadata["fix_suggestions"]).is_length(1)
    summary_kwargs = mock_generate_summary.call_args.kwargs
    assert_that(summary_kwargs.get("max_tokens")).is_equal_to(4096)
    assert_that(summary_kwargs).contains_key("workspace_root")


# ---------------------------------------------------------------------------
# Summary attachment
# ---------------------------------------------------------------------------


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.orchestrator.generate_summary")
def test_summary_attachment_summary_attached_to_all_results_with_issues(
    mock_generate_summary,
    mock_get_provider,
    _mock_require_ai,
):
    """Verify the AI summary is attached to every result that has issues."""
    result_a = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[
            MockIssue(
                file="a.py",
                line=1,
                message="err",
                code="E501",
            ),
        ],
    )
    result_b = ToolResult(
        name="mypy",
        success=False,
        issues_count=1,
        issues=[
            MockIssue(
                file="b.py",
                line=2,
                message="err",
                code="E303",
            ),
        ],
    )
    config = LintroConfig(ai=AIConfig(enabled=True))
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()
    mock_generate_summary.return_value = AISummary(overview="overview")

    run_ai_enhancement(
        action=Action.CHECK,
        all_results=[result_a, result_b],
        lintro_config=config,
        logger=logger,
        output_format="json",
    )

    assert_that(result_a.ai_metadata).is_not_none()
    assert_that(result_b.ai_metadata).is_not_none()
    assert_that(result_a.ai_metadata).contains_key("summary")
    assert_that(result_b.ai_metadata).contains_key("summary")
    assert_that(result_a.ai_metadata["summary"]["overview"]).is_equal_to(  # type: ignore[index]  # assertpy is_not_none narrows this
        "overview",
    )
    assert_that(result_b.ai_metadata["summary"]["overview"]).is_equal_to(  # type: ignore[index]  # assertpy is_not_none narrows this
        "overview",
    )


# ---------------------------------------------------------------------------
# _log_fix_limit_message
# ---------------------------------------------------------------------------


def test_log_fix_limit_message_no_log_when_within_limit():
    """No console output when total_issues <= max_fix_issues."""
    logger = MagicMock()
    _log_fix_limit_message(
        logger=logger,
        total_issues=3,
        max_fix_issues=5,
    )
    logger.console_output.assert_not_called()


def test_log_fix_limit_message_no_log_when_exactly_at_limit():
    """No console output when total_issues == max_fix_issues."""
    logger = MagicMock()
    _log_fix_limit_message(
        logger=logger,
        total_issues=5,
        max_fix_issues=5,
    )
    logger.console_output.assert_not_called()


def test_log_fix_limit_message_logs_when_over_limit():
    """Logs skipped count when total_issues > max_fix_issues."""
    logger = MagicMock()
    _log_fix_limit_message(
        logger=logger,
        total_issues=10,
        max_fix_issues=5,
    )
    logger.console_output.assert_called_once()
    msg = logger.console_output.call_args[0][0]
    assert_that(msg).contains("5 of")
    assert_that(msg).contains("10")
    assert_that(msg).contains("5 skipped")


# ---------------------------------------------------------------------------
# Integration: end-to-end check with real summary generation
# ---------------------------------------------------------------------------


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
def test_integration_orchestrator_end_to_end_check_with_real_summary_generation(
    mock_get_provider,
    _mock_require_ai,
):
    """Verify the real code path executes with only the provider mocked."""
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[
            MockIssue(
                file="src/main.py",
                line=1,
                message="Use of assert",
                code="B101",
                severity="low",
            ),
        ],
    )

    summary_response = AIResponse(
        content=json.dumps(
            {
                "overview": "Found 1 issue",
                "key_patterns": ["assert usage"],
                "priority_actions": ["Replace asserts"],
                "triage_suggestions": [],
                "estimated_effort": "5 minutes",
            },
        ),
        model="mock-model",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.002,
        provider="mock",
    )

    mock_get_provider.return_value = MockAIProvider(responses=[summary_response])
    config = LintroConfig(ai=AIConfig(enabled=True))
    logger = MagicMock()

    run_ai_enhancement(
        action=Action.CHECK,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
    )

    assert_that(result.ai_metadata).is_not_none()
    assert_that(result.ai_metadata).contains_key("summary")
    assert_that(result.ai_metadata["summary"]["overview"]).is_equal_to(  # type: ignore[index]  # assertpy is_not_none narrows this
        "Found 1 issue",
    )
