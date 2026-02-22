"""Tests for AI orchestration flow."""

from __future__ import annotations

import threading
from typing import Any
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
from lintro.ai.rerun import _rerun_cwd_lock, paths_for_context, rerun_tools
from lintro.ai.validation import ValidationResult
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import MockAIProvider, MockIssue

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _single_issue_result():
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
def _check_config():
    """LintroConfig with AI enabled and max_fix_issues=5."""
    return LintroConfig(ai=AIConfig(enabled=True, max_fix_issues=5))


@pytest.fixture
def _mock_logger():
    """MagicMock logger."""
    return MagicMock()


# ---------------------------------------------------------------------------
# TestRunAIEnhancement
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
    _single_issue_result,
    _check_config,
    _mock_logger,
):
    """Verify check+fix action attaches both summary and fix metadata to the result."""
    result = _single_issue_result
    config = _check_config
    logger = _mock_logger

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

    assert result.ai_metadata is not None
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
@patch("lintro.ai.pipeline.validate_applied_fixes")
@patch("lintro.ai.pipeline.apply_fixes")
def test_run_ai_enhancement_fix_action_generates_fix_metadata(
    mock_apply_fixes,
    mock_validate_applied_fixes,
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
    mock_validate_applied_fixes.return_value = ValidationResult(
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

    assert result.ai_metadata is not None
    assert_that(result.ai_metadata).contains_key("fix_suggestions")
    assert_that(result.ai_metadata).contains_key("applied_count")
    assert_that(result.ai_metadata).contains_key("verified_count")
    assert_that(result.ai_metadata).contains_key("unverified_count")
    assert_that(result.ai_metadata["fix_suggestions"]).is_length(1)
    assert_that(result.ai_metadata["applied_count"]).is_equal_to(1)
    assert_that(result.ai_metadata["verified_count"]).is_equal_to(1)
    assert_that(result.ai_metadata["unverified_count"]).is_equal_to(0)


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes")
@patch("lintro.ai.pipeline.apply_fixes")
@patch("lintro.ai.pipeline.review_fixes_interactive")
@patch("lintro.ai.pipeline.sys.stdin.isatty", return_value=False)
def test_run_ai_enhancement_fix_action_noninteractive_applies_safe_then_reviews_risky(
    _mock_isatty,
    mock_review_fixes_interactive,
    mock_apply_fixes,
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """Non-interactive mode auto-applies safe fixes, reviews risky."""
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=2,
        issues=[
            MockIssue(
                file="src/main.py",
                line=1,
                message="Line too long",
                code="E501",
            ),
            MockIssue(
                file="src/main.py",
                line=2,
                message="Use of assert",
                code="B101",
            ),
        ],
    )
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            auto_apply=False,
            auto_apply_safe_fixes=True,
        ),
    )
    logger = MagicMock()

    safe_suggestion = AIFixSuggestion(
        file="src/main.py",
        line=1,
        code="E501",
        explanation="Break long line",
        risk_level="safe-style",
        confidence="high",
    )
    risky_suggestion = AIFixSuggestion(
        file="src/main.py",
        line=2,
        code="B101",
        explanation="Replace assert",
        risk_level="behavioral-risk",
        confidence="medium",
    )

    mock_get_provider.return_value = MockAIProvider()
    mock_generate_fixes.return_value = [safe_suggestion, risky_suggestion]
    mock_apply_fixes.return_value = [safe_suggestion]
    mock_review_fixes_interactive.return_value = (0, 0, [])

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="terminal",
    )

    assert_that(mock_apply_fixes.call_count).is_equal_to(1)
    safe_batch = mock_apply_fixes.call_args.args[0]
    assert_that(safe_batch).is_length(1)
    assert_that(safe_batch[0].code).is_equal_to("E501")

    assert_that(mock_review_fixes_interactive.call_count).is_equal_to(1)
    risky_batch = mock_review_fixes_interactive.call_args.args[0]
    assert_that(risky_batch).is_length(1)
    assert_that(risky_batch[0].code).is_equal_to("B101")


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes")
@patch("lintro.ai.pipeline.apply_fixes")
@patch("lintro.ai.pipeline.validate_applied_fixes")
@patch("lintro.ai.pipeline.rerun_tools")
def test_run_ai_enhancement_fix_action_json_auto_applies_safe_style_suggestions(
    mock_rerun_tools,
    mock_validate_applied_fixes,
    mock_apply_fixes,
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """JSON mode auto-applies only safe-style suggestions and reruns."""
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=2,
        issues=[
            MockIssue(
                file="src/main.py",
                line=1,
                message="Line too long",
                code="E501",
            ),
            MockIssue(
                file="src/main.py",
                line=2,
                message="Use of assert",
                code="B101",
            ),
        ],
    )
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            max_fix_issues=5,
            auto_apply=False,
            auto_apply_safe_fixes=True,
        ),
    )
    logger = MagicMock()

    safe_suggestion = AIFixSuggestion(
        file="src/main.py",
        line=1,
        code="E501",
        explanation="Break long line",
        risk_level="safe-style",
        confidence="high",
    )
    risky_suggestion = AIFixSuggestion(
        file="src/main.py",
        line=2,
        code="B101",
        explanation="Replace assert",
        risk_level="behavioral-risk",
        confidence="medium",
    )

    mock_get_provider.return_value = MockAIProvider()
    mock_generate_fixes.return_value = [safe_suggestion, risky_suggestion]
    mock_apply_fixes.return_value = [safe_suggestion]
    mock_validate_applied_fixes.return_value = ValidationResult()
    mock_rerun_tools.return_value = [
        ToolResult(name="ruff", success=True, issues_count=0, issues=[]),
    ]

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
    )

    assert_that(mock_apply_fixes.call_count).is_equal_to(1)
    applied_batch = mock_apply_fixes.call_args.args[0]
    assert_that(applied_batch).is_length(1)
    assert_that(applied_batch[0].code).is_equal_to("E501")
    assert_that(mock_rerun_tools.call_count).is_equal_to(1)
    assert_that(mock_validate_applied_fixes.call_count).is_equal_to(1)
    assert result.ai_metadata is not None
    assert_that(result.ai_metadata).contains_key("fixed_count")
    assert_that(result.ai_metadata["fixed_count"]).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(result.issues_count).is_equal_to(0)


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes")
@patch("lintro.ai.pipeline.apply_fixes")
@patch("lintro.ai.pipeline.validate_applied_fixes")
@patch("lintro.ai.pipeline.rerun_tools")
def test_run_ai_enhancement_fix_action_json_uses_fresh_rerun_results(
    mock_rerun_tools,
    mock_validate_applied_fixes,
    mock_apply_fixes,
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """Verify JSON fix action updates result counts from fresh rerun tool output."""
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
        remaining_issues_count=1,
    )
    suggestion = AIFixSuggestion(
        file="src/main.py",
        line=1,
        code="B101",
        explanation="Replace assert",
        tool_name="ruff",
    )
    rerun_result = ToolResult(
        name="ruff",
        success=True,
        issues_count=0,
        issues=[],
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
    mock_validate_applied_fixes.return_value = ValidationResult(
        verified=1,
        unverified=0,
        verified_by_tool={"ruff": 1},
        unverified_by_tool={"ruff": 0},
    )
    mock_rerun_tools.return_value = [rerun_result]

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
    )

    assert_that(mock_rerun_tools.call_count).is_equal_to(1)
    assert_that(mock_validate_applied_fixes.call_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.success).is_true()


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
@patch("lintro.ai.pipeline.validate_applied_fixes")
@patch("lintro.ai.pipeline.rerun_tools")
@patch("lintro.ai.pipeline.generate_post_fix_summary")
def test_run_ai_enhancement_fix_action_uses_fresh_rerun_results_for_post_summary(
    mock_generate_post_fix_summary,
    mock_rerun_tools,
    mock_validate_applied_fixes,
    mock_apply_fixes,
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """Post-fix summary receives fresh rerun results, not stale."""
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
    rerun_result = ToolResult(
        name="ruff",
        success=True,
        issues_count=0,
        issues=[],
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
    mock_validate_applied_fixes.return_value = ValidationResult(
        verified=1,
        unverified=0,
        verified_by_tool={"ruff": 1},
        unverified_by_tool={"ruff": 0},
    )
    mock_rerun_tools.return_value = [rerun_result]
    mock_generate_post_fix_summary.return_value = None

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="terminal",
    )

    assert_that(mock_rerun_tools.call_count).is_equal_to(1)
    post_kwargs = mock_generate_post_fix_summary.call_args.kwargs
    assert_that(post_kwargs.get("remaining_results")).is_equal_to([rerun_result])


# ---------------------------------------------------------------------------
# TestRerunContext
# ---------------------------------------------------------------------------


def test_rerun_context_paths_for_context_relativizes_to_tool_cwd(tmp_path):
    """Paths inside tool cwd become relative; outside stay absolute."""
    tool_cwd = tmp_path / "tool"
    tool_cwd.mkdir(parents=True)
    inside = tool_cwd / "src" / "main.py"
    inside.parent.mkdir(parents=True)
    inside.write_text("x = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")

    rerun_paths = paths_for_context(
        file_paths=[str(inside), str(outside)],
        cwd=str(tool_cwd),
    )

    assert_that(rerun_paths[0]).is_equal_to("src/main.py")
    assert_that(rerun_paths[1]).is_equal_to(str(outside.resolve()))


@patch("lintro.tools.tool_manager.get_tool")
def test_rerun_context_rerun_uses_original_tool_cwd(mock_get_tool, tmp_path):
    """Verify rerun_tools changes cwd to the original tool working directory."""
    tool_cwd = tmp_path / "tool"
    tool_cwd.mkdir(parents=True)
    source = tool_cwd / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n", encoding="utf-8")

    issue = MockIssue(
        file=str(source),
        line=1,
        code="E501",
        message="Line too long",
    )
    original_result = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[issue],
        remaining_issues_count=1,
        cwd=str(tool_cwd),
    )
    by_tool = {"ruff": (original_result, [issue])}

    captured: dict[str, object] = {}

    class _FakeTool:
        def check(self, paths: Any, options: Any) -> ToolResult:
            import os

            captured["cwd"] = os.getcwd()
            captured["paths"] = paths
            return ToolResult(
                name="ruff",
                success=True,
                issues_count=0,
                issues=[],
            )

    mock_get_tool.return_value = _FakeTool()
    rerun_results = rerun_tools(by_tool)  # type: ignore[arg-type]  # test uses simplified mock data

    assert_that(rerun_results).is_length(1)
    assert_that(captured.get("cwd")).is_equal_to(str(tool_cwd))
    assert_that(captured.get("paths")).is_equal_to(["src/main.py"])


def test_rerun_context_rerun_cwd_lock_exists():
    """Verify the module-level threading lock is a Lock instance."""
    assert_that(_rerun_cwd_lock).is_instance_of(type(threading.Lock()))


@patch("lintro.tools.tool_manager.get_tool")
def test_rerun_context_rerun_continues_on_tool_failure(mock_get_tool, tmp_path):
    """When one tool fails, other tools still get rerun."""
    issue_a = MockIssue(
        file=str(tmp_path / "a.py"),
        line=1,
        code="E501",
        message="err",
    )
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    issue_b = MockIssue(
        file=str(tmp_path / "b.py"),
        line=1,
        code="E501",
        message="err",
    )
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")

    result_a = ToolResult(name="failing-tool", success=False, issues=[issue_a])
    result_b = ToolResult(name="passing-tool", success=False, issues=[issue_b])

    call_count = {"failing": 0, "passing": 0}

    class _FailingTool:
        def check(self, paths: Any, options: Any) -> ToolResult:
            call_count["failing"] += 1
            raise RuntimeError("boom")

    class _PassingTool:
        def check(self, paths: Any, options: Any) -> ToolResult:
            call_count["passing"] += 1
            return ToolResult(
                name="passing-tool",
                success=True,
                issues_count=0,
                issues=[],
            )

    def _side_effect(name):
        if name == "failing-tool":
            return _FailingTool()
        return _PassingTool()

    mock_get_tool.side_effect = _side_effect

    by_tool = {
        "failing-tool": (result_a, [issue_a]),
        "passing-tool": (result_b, [issue_b]),
    }
    rerun_results = rerun_tools(by_tool)  # type: ignore[arg-type]  # test uses simplified mock data

    assert_that(call_count["failing"]).is_equal_to(1)
    assert_that(call_count["passing"]).is_equal_to(1)
    assert rerun_results is not None
    assert_that(rerun_results).is_length(1)
    assert_that(rerun_results[0].name).is_equal_to("passing-tool")


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

    assert result_a.ai_metadata is not None
    assert result_b.ai_metadata is not None
    assert_that(result_a.ai_metadata).contains_key("summary")
    assert_that(result_b.ai_metadata).contains_key("summary")
    assert_that(result_a.ai_metadata["summary"]["overview"]).is_equal_to(
        "overview",
    )
    assert_that(result_b.ai_metadata["summary"]["overview"]).is_equal_to(
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

    assert result.ai_metadata is not None
    assert_that(result.ai_metadata).contains_key("summary")
    assert_that(result.ai_metadata["summary"]["overview"]).is_equal_to(
        "Found 1 issue",
    )
