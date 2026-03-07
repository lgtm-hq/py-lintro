"""Tests verifying config knobs flow through to the functions that use them.

Phase 3.4: After context_lines, fix_search_radius, retry delays, and
timeout were wired (Phase 2.1-2.5), these tests confirm the values
actually arrive at the downstream functions.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.models import AIFixSuggestion
from lintro.ai.pipeline import run_fix_pipeline
from lintro.ai.validation import ValidationResult
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from tests.unit.ai.conftest import MockAIProvider, MockIssue

_PIPELINE = "lintro.ai.pipeline"


def _make_suggestion(
    *,
    tool_name: str = "ruff",
    code: str = "E501",
) -> AIFixSuggestion:
    s = AIFixSuggestion(file="a.py", line=1, code=code)
    s.tool_name = tool_name
    return s


def _make_fix_issues() -> tuple[
    list[tuple[ToolResult, BaseIssue]],
    ToolResult,
    MockIssue,
]:
    issue = MockIssue(file="a.py", line=1, code="E501", message="err")
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[issue],
    )
    return [(result, issue)], result, issue


# -- context_lines wiring ---------------------------------------------------


@patch(f"{_PIPELINE}.render_validation")
@patch(f"{_PIPELINE}.render_summary")
@patch(f"{_PIPELINE}.validate_applied_fixes")
@patch(f"{_PIPELINE}.apply_rerun_results")
@patch(f"{_PIPELINE}.rerun_tools")
@patch(f"{_PIPELINE}.generate_post_fix_summary")
@patch(f"{_PIPELINE}.review_fixes_interactive")
@patch(f"{_PIPELINE}.apply_fixes")
@patch(f"{_PIPELINE}.generate_fixes")
def test_context_lines_flows_to_generate_fixes(
    mock_generate_fixes,
    mock_apply_fixes,
    _mock_review,
    _mock_post_summary,
    _mock_rerun,
    _mock_apply_rerun,
    _mock_validate,
    _mock_render_summary,
    _mock_render_validation,
):
    """ai_config.context_lines is passed through to generate_fixes()."""
    fix_issues, _result, _issue = _make_fix_issues()
    mock_generate_fixes.return_value = []

    ai_config = AIConfig(enabled=True, context_lines=42)

    run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=MagicMock(),
        output_format="json",
        workspace_root=Path("/tmp"),
    )

    kwargs = mock_generate_fixes.call_args.kwargs
    assert_that(kwargs["context_lines"]).is_equal_to(42)


# -- fix_search_radius wiring -----------------------------------------------


@patch(f"{_PIPELINE}.render_validation")
@patch(f"{_PIPELINE}.render_summary")
@patch(f"{_PIPELINE}.validate_applied_fixes")
@patch(f"{_PIPELINE}.apply_rerun_results")
@patch(f"{_PIPELINE}.rerun_tools")
@patch(f"{_PIPELINE}.generate_post_fix_summary")
@patch(f"{_PIPELINE}.review_fixes_interactive")
@patch(f"{_PIPELINE}.apply_fixes")
@patch(f"{_PIPELINE}.generate_fixes")
def test_fix_search_radius_flows_to_apply_fixes(
    mock_generate_fixes,
    mock_apply_fixes,
    _mock_review,
    _mock_post_summary,
    _mock_rerun,
    _mock_apply_rerun,
    _mock_validate,
    _mock_render_summary,
    _mock_render_validation,
):
    """ai_config.fix_search_radius is passed through to apply_fixes()."""
    fix_issues, _result, _issue = _make_fix_issues()
    suggestion = _make_suggestion()
    mock_generate_fixes.return_value = [suggestion]
    mock_apply_fixes.return_value = [suggestion]
    _mock_rerun.return_value = None
    _mock_validate.return_value = ValidationResult()

    ai_config = AIConfig(
        enabled=True,
        auto_apply=True,
        fix_search_radius=25,
    )

    run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=MagicMock(),
        output_format="json",
        workspace_root=Path("/tmp"),
    )

    kwargs = mock_apply_fixes.call_args.kwargs
    assert_that(kwargs["search_radius"]).is_equal_to(25)


# -- retry delay wiring -----------------------------------------------------


@patch(f"{_PIPELINE}.render_validation")
@patch(f"{_PIPELINE}.render_summary")
@patch(f"{_PIPELINE}.validate_applied_fixes")
@patch(f"{_PIPELINE}.apply_rerun_results")
@patch(f"{_PIPELINE}.rerun_tools")
@patch(f"{_PIPELINE}.generate_post_fix_summary")
@patch(f"{_PIPELINE}.review_fixes_interactive")
@patch(f"{_PIPELINE}.apply_fixes")
@patch(f"{_PIPELINE}.generate_fixes")
def test_retry_delays_flow_to_generate_fixes(
    mock_generate_fixes,
    mock_apply_fixes,
    _mock_review,
    _mock_post_summary,
    _mock_rerun,
    _mock_apply_rerun,
    _mock_validate,
    _mock_render_summary,
    _mock_render_validation,
):
    """Retry delay config values are passed through to generate_fixes()."""
    fix_issues, _result, _issue = _make_fix_issues()
    mock_generate_fixes.return_value = []

    ai_config = AIConfig(
        enabled=True,
        retry_base_delay=0.5,
        retry_max_delay=10.0,
        retry_backoff_factor=3.0,
    )

    run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=MagicMock(),
        output_format="json",
        workspace_root=Path("/tmp"),
    )

    kwargs = mock_generate_fixes.call_args.kwargs
    assert_that(kwargs["base_delay"]).is_equal_to(0.5)
    assert_that(kwargs["max_delay"]).is_equal_to(10.0)
    assert_that(kwargs["backoff_factor"]).is_equal_to(3.0)


# -- timeout wiring to post-fix summary ------------------------------------


@patch(f"{_PIPELINE}.render_validation")
@patch(f"{_PIPELINE}.render_summary")
@patch(f"{_PIPELINE}.validate_applied_fixes")
@patch(f"{_PIPELINE}.apply_rerun_results")
@patch(f"{_PIPELINE}.rerun_tools")
@patch(f"{_PIPELINE}.generate_post_fix_summary")
@patch(f"{_PIPELINE}.review_fixes_interactive")
@patch(f"{_PIPELINE}.apply_fixes")
@patch(f"{_PIPELINE}.generate_fixes")
def test_timeout_and_retries_flow_to_post_fix_summary(
    mock_generate_fixes,
    mock_apply_fixes,
    _mock_review,
    mock_post_summary,
    mock_rerun,
    _mock_apply_rerun,
    mock_validate,
    _mock_render_summary,
    _mock_render_validation,
):
    """api_timeout and retry config flow through to generate_post_fix_summary()."""
    fix_issues, _result, _issue = _make_fix_issues()
    suggestion = _make_suggestion()
    mock_generate_fixes.return_value = [suggestion]
    mock_apply_fixes.return_value = [suggestion]
    mock_rerun.return_value = [
        ToolResult(name="ruff", success=True, issues_count=0, issues=[]),
    ]
    mock_validate.return_value = ValidationResult(
        verified=1,
        unverified=0,
        verified_by_tool={"ruff": 1},
        unverified_by_tool={"ruff": 0},
    )
    mock_post_summary.return_value = None

    ai_config = AIConfig(
        enabled=True,
        auto_apply=True,
        api_timeout=120.0,
        max_retries=5,
        retry_base_delay=2.0,
        retry_max_delay=60.0,
        retry_backoff_factor=4.0,
    )

    run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=MagicMock(),
        output_format="terminal",
        workspace_root=Path("/tmp"),
    )

    kwargs = mock_post_summary.call_args.kwargs
    assert_that(kwargs["timeout"]).is_equal_to(120.0)
    assert_that(kwargs["max_retries"]).is_equal_to(5)
    assert_that(kwargs["base_delay"]).is_equal_to(2.0)
    assert_that(kwargs["max_delay"]).is_equal_to(60.0)
    assert_that(kwargs["backoff_factor"]).is_equal_to(4.0)


# -- timeout wiring to summary in orchestrator -----------------------------


@patch("lintro.ai.orchestrator.run_fix_pipeline")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.generate_summary")
def test_timeout_and_retries_flow_to_generate_summary(
    mock_summary,
    _mock_require,
    mock_get_provider,
    _mock_pipeline,
):
    """api_timeout and retry config flow through to generate_summary()."""
    from lintro.ai.orchestrator import run_ai_enhancement
    from lintro.config.lintro_config import LintroConfig
    from lintro.enums.action import Action

    mock_get_provider.return_value = MockAIProvider()
    mock_summary.return_value = None

    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            api_timeout=90.0,
            max_retries=4,
            retry_base_delay=1.5,
            retry_max_delay=20.0,
            retry_backoff_factor=2.5,
        ),
    )

    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[MockIssue(file="x.py", line=1, message="err", code="E501")],
    )

    run_ai_enhancement(
        action=Action.CHECK,
        all_results=[result],
        lintro_config=config,
        logger=MagicMock(),
        output_format="terminal",
    )

    kwargs = mock_summary.call_args.kwargs
    assert_that(kwargs["timeout"]).is_equal_to(90.0)
    assert_that(kwargs["max_retries"]).is_equal_to(4)
    assert_that(kwargs["base_delay"]).is_equal_to(1.5)
    assert_that(kwargs["max_delay"]).is_equal_to(20.0)
    assert_that(kwargs["backoff_factor"]).is_equal_to(2.5)
