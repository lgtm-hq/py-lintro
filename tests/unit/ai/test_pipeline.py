"""Tests for the AI fix pipeline (lintro.ai.pipeline.run_fix_pipeline)."""

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PIPELINE = "lintro.ai.pipeline"


def _make_suggestion(
    *,
    file: str = "src/main.py",
    line: int = 1,
    code: str = "E501",
    tool_name: str = "ruff",
    risk_level: str = "",
    confidence: str = "high",
    explanation: str = "fix",
) -> AIFixSuggestion:
    s = AIFixSuggestion(
        file=file,
        line=line,
        code=code,
        explanation=explanation,
        risk_level=risk_level,
        confidence=confidence,
    )
    s.tool_name = tool_name
    return s


def _default_ai_config(**overrides: object) -> AIConfig:
    defaults: dict[str, object] = {
        "enabled": True,
        "max_fix_issues": 20,
    }
    defaults.update(overrides)
    return AIConfig(**defaults)  # type: ignore[arg-type]


def _make_result(name: str, issues: list[MockIssue]) -> ToolResult:
    return ToolResult(
        name=name,
        success=False,
        issues_count=len(issues),
        issues=issues,
    )


def _make_fix_issues(
    result: ToolResult,
    issues: list[MockIssue],
) -> list[tuple[ToolResult, BaseIssue]]:
    return [(result, issue) for issue in issues]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@patch(f"{_PIPELINE}.render_validation")
@patch(f"{_PIPELINE}.render_summary")
@patch(f"{_PIPELINE}.validate_applied_fixes")
@patch(f"{_PIPELINE}.apply_rerun_results")
@patch(f"{_PIPELINE}.rerun_tools")
@patch(f"{_PIPELINE}.generate_post_fix_summary")
@patch(f"{_PIPELINE}.review_fixes_interactive")
@patch(f"{_PIPELINE}.apply_fixes")
@patch(f"{_PIPELINE}.generate_fixes")
def test_budget_tracking_across_multiple_tools(
    mock_generate_fixes,
    mock_apply_fixes,
    mock_review_fixes_interactive,
    mock_generate_post_fix_summary,
    mock_rerun_tools,
    mock_apply_rerun_results,
    mock_validate_applied_fixes,
    mock_render_summary,
    mock_render_validation,
):
    """When two tools have issues, budget (max_fix_issues) is consumed correctly."""
    issue_a = MockIssue(file="a.py", line=1, code="E501", message="err")
    issue_b = MockIssue(file="b.py", line=1, code="E501", message="err")
    issue_c = MockIssue(file="c.py", line=1, code="W001", message="err")

    result_ruff = _make_result("ruff", [issue_a, issue_b])
    result_mypy = _make_result("mypy", [issue_c])

    fix_issues = _make_fix_issues(result_ruff, [issue_a, issue_b]) + _make_fix_issues(
        result_mypy,
        [issue_c],
    )

    suggestion_a = _make_suggestion(file="a.py", tool_name="ruff")
    suggestion_b = _make_suggestion(file="b.py", tool_name="ruff")
    suggestion_c = _make_suggestion(file="c.py", tool_name="mypy", code="W001")

    mock_generate_fixes.side_effect = [
        [suggestion_a, suggestion_b],
        [suggestion_c],
    ]
    mock_apply_fixes.return_value = []
    mock_review_fixes_interactive.return_value = (0, 0, [])
    mock_rerun_tools.return_value = None
    mock_validate_applied_fixes.return_value = ValidationResult()

    ai_config = _default_ai_config(max_fix_issues=3)

    run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=MagicMock(),
        output_format="terminal",
        workspace_root=Path("/tmp"),
    )

    assert_that(mock_generate_fixes.call_count).is_equal_to(2)

    # First call gets full budget of 3
    first_call_kwargs = mock_generate_fixes.call_args_list[0].kwargs
    assert_that(first_call_kwargs["max_issues"]).is_equal_to(3)

    # Second call gets reduced budget: 3 - 2 (issues consumed from ruff) = 1
    second_call_kwargs = mock_generate_fixes.call_args_list[1].kwargs
    assert_that(second_call_kwargs["max_issues"]).is_equal_to(1)


@patch(f"{_PIPELINE}.is_safe_style_fix")
@patch(f"{_PIPELINE}.render_validation")
@patch(f"{_PIPELINE}.render_summary")
@patch(f"{_PIPELINE}.validate_applied_fixes")
@patch(f"{_PIPELINE}.apply_rerun_results")
@patch(f"{_PIPELINE}.rerun_tools")
@patch(f"{_PIPELINE}.generate_post_fix_summary")
@patch(f"{_PIPELINE}.review_fixes_interactive")
@patch(f"{_PIPELINE}.apply_fixes")
@patch(f"{_PIPELINE}.generate_fixes")
def test_safe_vs_risky_suggestion_splitting(
    mock_generate_fixes,
    mock_apply_fixes,
    mock_review_fixes_interactive,
    mock_generate_post_fix_summary,
    mock_rerun_tools,
    mock_apply_rerun_results,
    mock_validate_applied_fixes,
    mock_render_summary,
    mock_render_validation,
    mock_is_safe,
):
    """Suggestions split into safe and risky via is_safe_style_fix."""
    issue = MockIssue(file="a.py", line=1, code="E501", message="err")
    result = _make_result("ruff", [issue])
    fix_issues = _make_fix_issues(result, [issue])

    safe = _make_suggestion(code="E501", risk_level="safe-style")
    risky = _make_suggestion(code="B101", risk_level="behavioral-risk")

    mock_generate_fixes.return_value = [safe, risky]
    mock_is_safe.side_effect = lambda s: s.risk_level == "safe-style"
    mock_apply_fixes.return_value = [safe]
    mock_review_fixes_interactive.return_value = (0, 0, [])
    mock_rerun_tools.return_value = None
    mock_validate_applied_fixes.return_value = ValidationResult()

    ai_config = _default_ai_config(auto_apply_safe_fixes=True)

    run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=MagicMock(),
        output_format="json",
        workspace_root=Path("/tmp"),
    )

    # apply_fixes is called with only safe suggestions for fast path
    applied_batch = mock_apply_fixes.call_args.args[0]
    assert_that(applied_batch).is_length(1)
    assert_that(applied_batch[0].risk_level).is_equal_to("safe-style")


@patch(f"{_PIPELINE}.render_validation")
@patch(f"{_PIPELINE}.render_summary")
@patch(f"{_PIPELINE}.validate_applied_fixes")
@patch(f"{_PIPELINE}.apply_rerun_results")
@patch(f"{_PIPELINE}.rerun_tools")
@patch(f"{_PIPELINE}.generate_post_fix_summary")
@patch(f"{_PIPELINE}.review_fixes_interactive")
@patch(f"{_PIPELINE}.apply_fixes")
@patch(f"{_PIPELINE}.generate_fixes")
def test_auto_apply_fast_path_json_mode(
    mock_generate_fixes,
    mock_apply_fixes,
    mock_review_fixes_interactive,
    mock_generate_post_fix_summary,
    mock_rerun_tools,
    mock_apply_rerun_results,
    mock_validate_applied_fixes,
    mock_render_summary,
    mock_render_validation,
):
    """Safe fixes auto-apply when auto_apply_safe_fixes + json."""
    issue = MockIssue(file="a.py", line=1, code="E501", message="err")
    result = _make_result("ruff", [issue])
    fix_issues = _make_fix_issues(result, [issue])

    safe = _make_suggestion(code="E501", risk_level="safe-style", confidence="high")

    mock_generate_fixes.return_value = [safe]
    mock_apply_fixes.return_value = [safe]
    mock_rerun_tools.return_value = None
    mock_validate_applied_fixes.return_value = ValidationResult()

    ai_config = _default_ai_config(auto_apply_safe_fixes=True, auto_apply=False)

    run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=MagicMock(),
        output_format="json",
        workspace_root=Path("/tmp"),
    )

    assert_that(mock_apply_fixes.call_count).is_greater_than_or_equal_to(1)
    apply_kwargs = mock_apply_fixes.call_args.kwargs
    assert_that(apply_kwargs["auto_apply"]).is_true()

    # review_fixes_interactive should NOT be called in json mode
    mock_review_fixes_interactive.assert_not_called()


@patch(f"{_PIPELINE}.render_validation")
@patch(f"{_PIPELINE}.render_summary")
@patch(f"{_PIPELINE}.validate_applied_fixes")
@patch(f"{_PIPELINE}.apply_rerun_results")
@patch(f"{_PIPELINE}.rerun_tools")
@patch(f"{_PIPELINE}.generate_post_fix_summary")
@patch(f"{_PIPELINE}.review_fixes_interactive")
@patch(f"{_PIPELINE}.apply_fixes")
@patch(f"{_PIPELINE}.generate_fixes")
@patch(f"{_PIPELINE}.sys.stdin.isatty", return_value=True)
def test_interactive_review_path(
    _mock_isatty,
    mock_generate_fixes,
    mock_apply_fixes,
    mock_review_fixes_interactive,
    mock_generate_post_fix_summary,
    mock_rerun_tools,
    mock_apply_rerun_results,
    mock_validate_applied_fixes,
    mock_render_summary,
    mock_render_validation,
):
    """When not json and not auto_apply, review_fixes_interactive is called."""
    issue = MockIssue(file="a.py", line=1, code="B101", message="err")
    result = _make_result("ruff", [issue])
    fix_issues = _make_fix_issues(result, [issue])

    suggestion = _make_suggestion(code="B101", risk_level="behavioral-risk")

    mock_generate_fixes.return_value = [suggestion]
    mock_review_fixes_interactive.return_value = (1, 0, [suggestion])
    mock_rerun_tools.return_value = None
    mock_validate_applied_fixes.return_value = ValidationResult()

    ai_config = _default_ai_config(auto_apply=False, auto_apply_safe_fixes=False)

    run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=MagicMock(),
        output_format="terminal",
        workspace_root=Path("/tmp"),
    )

    assert_that(mock_review_fixes_interactive.call_count).is_equal_to(1)
    review_batch = mock_review_fixes_interactive.call_args.args[0]
    assert_that(review_batch).is_length(1)
    assert_that(review_batch[0].code).is_equal_to("B101")


@patch(f"{_PIPELINE}.render_validation")
@patch(f"{_PIPELINE}.render_summary")
@patch(f"{_PIPELINE}.validate_applied_fixes")
@patch(f"{_PIPELINE}.apply_rerun_results")
@patch(f"{_PIPELINE}.rerun_tools")
@patch(f"{_PIPELINE}.generate_post_fix_summary")
@patch(f"{_PIPELINE}.review_fixes_interactive")
@patch(f"{_PIPELINE}.apply_fixes")
@patch(f"{_PIPELINE}.generate_fixes")
def test_no_suggestions_returns_early(
    mock_generate_fixes,
    mock_apply_fixes,
    mock_review_fixes_interactive,
    mock_generate_post_fix_summary,
    mock_rerun_tools,
    mock_apply_rerun_results,
    mock_validate_applied_fixes,
    mock_render_summary,
    mock_render_validation,
):
    """Empty generate_fixes exits without calling apply/review."""
    issue = MockIssue(file="a.py", line=1, code="E501", message="err")
    result = _make_result("ruff", [issue])
    fix_issues = _make_fix_issues(result, [issue])

    mock_generate_fixes.return_value = []

    ai_config = _default_ai_config()

    run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=MagicMock(),
        output_format="json",
        workspace_root=Path("/tmp"),
    )

    assert_that(mock_generate_fixes.call_count).is_equal_to(1)
    mock_apply_fixes.assert_not_called()
    mock_review_fixes_interactive.assert_not_called()
    mock_rerun_tools.assert_not_called()
    mock_validate_applied_fixes.assert_not_called()
    mock_generate_post_fix_summary.assert_not_called()


@patch(f"{_PIPELINE}.render_validation")
@patch(f"{_PIPELINE}.render_summary")
@patch(f"{_PIPELINE}.validate_applied_fixes")
@patch(f"{_PIPELINE}.apply_rerun_results")
@patch(f"{_PIPELINE}.rerun_tools")
@patch(f"{_PIPELINE}.generate_post_fix_summary")
@patch(f"{_PIPELINE}.review_fixes_interactive")
@patch(f"{_PIPELINE}.apply_fixes")
@patch(f"{_PIPELINE}.generate_fixes")
def test_post_fix_summary_generation(
    mock_generate_fixes,
    mock_apply_fixes,
    mock_review_fixes_interactive,
    mock_generate_post_fix_summary,
    mock_rerun_tools,
    mock_apply_rerun_results,
    mock_validate_applied_fixes,
    mock_render_summary,
    mock_render_validation,
):
    """Applied fixes + non-json → post_fix_summary is called."""
    issue = MockIssue(file="a.py", line=1, code="B101", message="err")
    result = _make_result("ruff", [issue])
    fix_issues = _make_fix_issues(result, [issue])

    suggestion = _make_suggestion(code="B101", tool_name="ruff")

    mock_generate_fixes.return_value = [suggestion]
    mock_apply_fixes.return_value = [suggestion]
    mock_rerun_tools.return_value = [
        ToolResult(name="ruff", success=True, issues_count=0, issues=[]),
    ]
    mock_validate_applied_fixes.return_value = ValidationResult(
        verified=1,
        unverified=0,
        verified_by_tool={"ruff": 1},
        unverified_by_tool={"ruff": 0},
    )
    mock_generate_post_fix_summary.return_value = None

    ai_config = _default_ai_config(auto_apply=True)

    run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=MagicMock(),
        output_format="terminal",
        workspace_root=Path("/tmp"),
    )

    assert_that(mock_generate_post_fix_summary.call_count).is_equal_to(1)
    post_kwargs = mock_generate_post_fix_summary.call_args.kwargs
    assert_that(post_kwargs).contains_key("remaining_results")
    assert_that(post_kwargs).contains_key("applied")
    assert_that(post_kwargs).contains_key("rejected")


@patch(f"{_PIPELINE}.render_validation")
@patch(f"{_PIPELINE}.render_summary")
@patch(f"{_PIPELINE}.validate_applied_fixes")
@patch(f"{_PIPELINE}.apply_rerun_results")
@patch(f"{_PIPELINE}.rerun_tools")
@patch(f"{_PIPELINE}.generate_post_fix_summary")
@patch(f"{_PIPELINE}.review_fixes_interactive")
@patch(f"{_PIPELINE}.apply_fixes")
@patch(f"{_PIPELINE}.generate_fixes")
def test_rerun_and_validation_flow(
    mock_generate_fixes,
    mock_apply_fixes,
    mock_review_fixes_interactive,
    mock_generate_post_fix_summary,
    mock_rerun_tools,
    mock_apply_rerun_results,
    mock_validate_applied_fixes,
    mock_render_summary,
    mock_render_validation,
):
    """When fixes are applied, rerun_tools and validate_applied_fixes are called."""
    issue = MockIssue(file="a.py", line=1, code="B101", message="err")
    result = _make_result("ruff", [issue])
    fix_issues = _make_fix_issues(result, [issue])

    suggestion = _make_suggestion(code="B101", tool_name="ruff")

    mock_generate_fixes.return_value = [suggestion]
    mock_apply_fixes.return_value = [suggestion]
    rerun_result = ToolResult(
        name="ruff",
        success=True,
        issues_count=0,
        issues=[],
    )
    mock_rerun_tools.return_value = [rerun_result]
    mock_validate_applied_fixes.return_value = ValidationResult(
        verified=1,
        unverified=0,
        verified_by_tool={"ruff": 1},
        unverified_by_tool={"ruff": 0},
    )
    mock_generate_post_fix_summary.return_value = None

    ai_config = _default_ai_config(auto_apply=True)

    run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=MagicMock(),
        output_format="terminal",
        workspace_root=Path("/tmp"),
    )

    assert_that(mock_rerun_tools.call_count).is_equal_to(1)
    assert_that(mock_validate_applied_fixes.call_count).is_equal_to(1)
    validate_args = mock_validate_applied_fixes.call_args.args
    assert_that(validate_args[0]).is_equal_to([suggestion])

    assert_that(mock_apply_rerun_results.call_count).is_equal_to(1)
    rerun_kwargs = mock_apply_rerun_results.call_args.kwargs
    assert_that(rerun_kwargs).contains_key("by_tool")
    assert_that(rerun_kwargs).contains_key("rerun_results")
    assert_that(rerun_kwargs["rerun_results"]).is_equal_to([rerun_result])
