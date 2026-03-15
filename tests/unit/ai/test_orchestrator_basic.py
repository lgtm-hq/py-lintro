"""Tests for basic AI orchestration scenarios (single tool, simple flows)."""

from __future__ import annotations

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
from lintro.ai.validation import ValidationResult
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
# TestRunAIEnhancement — basic single-tool scenarios
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


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes")
@patch("lintro.ai.pipeline.verify_fixes")
@patch("lintro.ai.pipeline.apply_fixes")
def test_run_ai_enhancement_fix_action_generates_fix_metadata(
    mock_apply_fixes,
    mock_verify_fixes,
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """Verify fix action populates applied/verified counts."""
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
            ),
        ],
    )
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            max_fix_issues=5,
            auto_apply=True,
        ),
    )
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()
    suggestion = AIFixSuggestion(
        file="src/main.py",
        line=1,
        code="B101",
        explanation="Replace assert",
    )
    suggestion.tool_name = "ruff"
    mock_generate_fixes.return_value = [suggestion]
    mock_apply_fixes.return_value = [suggestion]
    mock_verify_fixes.return_value = ValidationResult(
        verified=1,
        unverified=0,
        verified_by_tool={"ruff": 1},
        unverified_by_tool={"ruff": 0},
    )

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="terminal",
    )

    assert_that(result.ai_metadata).is_not_none()
    assert_that(result.ai_metadata).contains_key("fix_suggestions")
    assert_that(result.ai_metadata).contains_key("applied_count")
    assert_that(result.ai_metadata).contains_key("verified_count")
    assert_that(result.ai_metadata).contains_key("unverified_count")
    assert_that(result.ai_metadata["fix_suggestions"]).is_length(1)  # type: ignore[index]  # assertpy is_not_none narrows this
    assert_that(result.ai_metadata["applied_count"]).is_equal_to(1)  # type: ignore[index]  # assertpy is_not_none narrows this
    assert_that(result.ai_metadata["verified_count"]).is_equal_to(1)  # type: ignore[index]  # assertpy is_not_none narrows this
    assert_that(result.ai_metadata["unverified_count"]).is_equal_to(0)  # type: ignore[index]  # assertpy is_not_none narrows this


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes")
@patch("lintro.ai.pipeline.review_fixes_interactive")
@patch("lintro.ai.pipeline.sys.stdin.isatty", return_value=True)
def test_run_ai_enhancement_fix_action_passes_validate_mode_to_interactive_review(
    _mock_isatty,
    mock_review_fixes_interactive,
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """Verify validate_after_group config flag is forwarded to interactive review."""
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
            ),
        ],
    )
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            max_fix_issues=5,
            validate_after_group=True,
        ),
    )
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()
    mock_generate_fixes.return_value = [
        AIFixSuggestion(
            file="src/main.py",
            line=1,
            code="B101",
            explanation="Replace assert",
        ),
    ]
    mock_review_fixes_interactive.return_value = (0, 0, [])

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="terminal",
    )

    assert_that(mock_review_fixes_interactive.call_count).is_equal_to(1)
    kwargs = mock_review_fixes_interactive.call_args.kwargs
    assert_that(kwargs.get("validate_after_group")).is_true()


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes")
def test_run_ai_enhancement_fix_action_uses_only_remaining_issue_tail(
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """Fix generation receives only remaining issues, not fixed."""
    fixed_issue = MockIssue(
        file="src/main.py",
        line=1,
        message="Already fixed",
        code="FORMAT",
    )
    remaining_issue = MockIssue(
        file="src/main.py",
        line=2,
        message="Still failing",
        code="E501",
    )
    result = ToolResult(
        name="prettier",
        success=False,
        issues_count=1,
        issues=[fixed_issue, remaining_issue],
        remaining_issues_count=1,
    )
    config = LintroConfig(ai=AIConfig(enabled=True, max_fix_issues=5))
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()
    mock_generate_fixes.return_value = []

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
    )

    assert_that(mock_generate_fixes.call_count).is_equal_to(1)
    issues_arg = mock_generate_fixes.call_args.args[0]
    assert_that(issues_arg).is_length(1)
    assert_that(issues_arg[0].code).is_equal_to("E501")
    fix_kwargs = mock_generate_fixes.call_args.kwargs
    assert_that(fix_kwargs.get("max_tokens")).is_equal_to(4096)
    assert_that(fix_kwargs).contains_key("workspace_root")


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes")
def test_run_ai_enhancement_fix_action_skips_tools_with_zero_remaining_issues(
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """Verify fix generation is skipped for tools with zero remaining issues."""
    result = ToolResult(
        name="prettier",
        success=True,
        issues_count=0,
        issues=[
            MockIssue(
                file="src/main.py",
                line=1,
                message="Initial issue",
                code="FORMAT",
            ),
        ],
        remaining_issues_count=0,
    )
    config = LintroConfig(ai=AIConfig(enabled=True, max_fix_issues=5))
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
    )

    mock_generate_fixes.assert_not_called()


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes")
@patch("lintro.ai.pipeline.apply_fixes")
@patch("lintro.ai.pipeline.verify_fixes")
@patch("lintro.ai.pipeline.generate_post_fix_summary")
def test_run_ai_enhancement_fix_action_uses_fresh_rerun_results_for_post_summary(
    mock_generate_post_fix_summary,
    mock_verify_fixes,
    mock_apply_fixes,
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """Post-fix summary receives results from by_tool after verify_fixes."""
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
            ),
        ],
    )
    suggestion = AIFixSuggestion(
        file="src/main.py",
        line=1,
        code="B101",
        explanation="Replace assert",
        tool_name="ruff",
    )
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            auto_apply=True,
        ),
    )
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()
    mock_generate_fixes.return_value = [suggestion]
    mock_apply_fixes.return_value = [suggestion]
    mock_verify_fixes.return_value = ValidationResult(
        verified=1,
        unverified=0,
        verified_by_tool={"ruff": 1},
        unverified_by_tool={"ruff": 0},
    )
    mock_generate_post_fix_summary.return_value = None

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="terminal",
    )

    assert_that(mock_verify_fixes.call_count).is_equal_to(1)
    assert_that(mock_generate_post_fix_summary.call_count).is_equal_to(1)
    post_kwargs = mock_generate_post_fix_summary.call_args.kwargs
    assert_that(post_kwargs.get("remaining_results")).is_not_none()


# ---------------------------------------------------------------------------
# TestSummaryAttachment
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
# TestLogFixLimitMessage
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
# TestIntegrationOrchestrator
# ---------------------------------------------------------------------------


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
def test_integration_orchestrator_end_to_end_check_with_real_summary_generation(
    mock_get_provider,
    _mock_require_ai,
):
    """Verify the real code path executes with only the provider mocked."""
    import json

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
