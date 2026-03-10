"""Tests for AI orchestration edge cases, error handling, and fail_on_unfixed."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.models import AIFixSuggestion, AIResult, AISummary
from lintro.ai.orchestrator import run_ai_enhancement
from lintro.ai.validation import ValidationResult
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import MockAIProvider, MockIssue

# ---------------------------------------------------------------------------
# TestAIResultExitCode
# ---------------------------------------------------------------------------


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.orchestrator.generate_summary")
def test_ai_result_default_no_error(
    mock_generate_summary,
    mock_get_provider,
    _mock_require_ai,
):
    """Default behavior: AI returns AIResult with no error flag."""
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
    config = LintroConfig(ai=AIConfig(enabled=True))
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()
    mock_generate_summary.return_value = AISummary(overview="AI overview")

    ai_result = run_ai_enhancement(
        action=Action.CHECK,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
    )

    assert_that(ai_result).is_instance_of(AIResult)
    assert_that(ai_result.error).is_false()
    assert_that(ai_result.fixes_applied).is_equal_to(0)
    assert_that(ai_result.fixes_failed).is_equal_to(0)
    assert_that(ai_result.unfixed_issues).is_equal_to(0)
    assert_that(ai_result.budget_exceeded).is_false()


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.orchestrator.generate_summary")
@patch("lintro.ai.pipeline.generate_fixes")
def test_ai_result_unfixed_issues_when_fixes_fail(
    mock_generate_fixes,
    mock_generate_summary,
    mock_get_provider,
    _mock_require_ai,
):
    """AIResult reports unfixed issues when fix generation returns nothing."""
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
        ai=AIConfig(enabled=True, max_fix_issues=5, fail_on_unfixed=True),
    )
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()
    mock_generate_summary.return_value = None
    mock_generate_fixes.return_value = []

    ai_result = run_ai_enhancement(
        action=Action.CHECK,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
        ai_fix=True,
    )

    assert_that(ai_result).is_instance_of(AIResult)
    assert_that(ai_result.unfixed_issues).is_equal_to(1)
    assert_that(ai_result.fixes_applied).is_equal_to(0)


def test_ai_result_error_on_exception():
    """AIResult.error is True when AI enhancement raises an exception."""
    config = LintroConfig(ai=AIConfig(enabled=True))
    logger = MagicMock()

    with patch(
        "lintro.ai.orchestrator.require_ai",
        side_effect=RuntimeError("boom"),
    ):
        ai_result = run_ai_enhancement(
            action=Action.CHECK,
            all_results=[],
            lintro_config=config,
            logger=logger,
            output_format="json",
        )

    assert_that(ai_result).is_instance_of(AIResult)
    assert_that(ai_result.error).is_true()


def test_ai_result_error_propagates_when_fail_on_ai_error():
    """Exceptions propagate when fail_on_ai_error=True."""
    config = LintroConfig(ai=AIConfig(enabled=True, fail_on_ai_error=True))
    logger = MagicMock()

    with (
        patch(
            "lintro.ai.orchestrator.require_ai",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        run_ai_enhancement(
            action=Action.CHECK,
            all_results=[],
            lintro_config=config,
            logger=logger,
            output_format="json",
        )


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes")
@patch("lintro.ai.pipeline.apply_fixes")
@patch("lintro.ai.pipeline.verify_fixes")
def test_ai_result_tracks_applied_fixes(
    mock_verify_fixes,
    mock_apply_fixes,
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """AIResult correctly reports fixes_applied and fixes_failed."""
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=2,
        issues=[
            MockIssue(
                file="src/main.py",
                line=1,
                message="Use of assert",
                code="B101",
            ),
            MockIssue(
                file="src/main.py",
                line=2,
                message="Line too long",
                code="E501",
            ),
        ],
        remaining_issues_count=2,
    )
    suggestion1 = AIFixSuggestion(
        file="src/main.py",
        line=1,
        code="B101",
        explanation="Replace assert",
        tool_name="ruff",
    )
    suggestion2 = AIFixSuggestion(
        file="src/main.py",
        line=2,
        code="E501",
        explanation="Break line",
        tool_name="ruff",
    )
    config = LintroConfig(
        ai=AIConfig(enabled=True, auto_apply=True),
    )
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()
    mock_generate_fixes.return_value = [suggestion1, suggestion2]
    # Only one fix applies successfully
    mock_apply_fixes.return_value = [suggestion1]
    mock_verify_fixes.return_value = ValidationResult(
        verified=1,
        unverified=0,
        verified_by_tool={"ruff": 1},
        unverified_by_tool={"ruff": 0},
    )

    ai_result = run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
    )

    assert_that(ai_result).is_instance_of(AIResult)
    assert_that(ai_result.fixes_applied).is_equal_to(1)
    assert_that(ai_result.fixes_failed).is_equal_to(1)
    assert_that(ai_result.unfixed_issues).is_equal_to(1)


# ---------------------------------------------------------------------------
# TestFailOnUnfixed
# ---------------------------------------------------------------------------


def test_fail_on_unfixed_config_default_is_false():
    """Verify fail_on_unfixed defaults to False."""
    config = AIConfig()
    assert_that(config.fail_on_unfixed).is_false()


def test_fail_on_unfixed_config_can_be_set():
    """Verify fail_on_unfixed can be set to True."""
    config = AIConfig(fail_on_unfixed=True)
    assert_that(config.fail_on_unfixed).is_true()
