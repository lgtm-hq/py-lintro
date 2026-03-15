"""Tests for AI orchestrator fix action."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.models import AIFixSuggestion
from lintro.ai.orchestrator import run_ai_enhancement
from lintro.ai.validation import ValidationResult
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import MockAIProvider, MockIssue

# ---------------------------------------------------------------------------
# Fix action tests
# ---------------------------------------------------------------------------


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
