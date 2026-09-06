"""Tests for multi-tool AI orchestration scenarios and complex workflows."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.models import AIFixSuggestion
from lintro.ai.orchestrator import run_ai_enhancement
from lintro.ai.rerun import absolute_paths_for_context, rerun_tools
from lintro.ai.validation import ValidationResult
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from tests.unit.ai.conftest import (
    MockAIProvider,
    MockIssue,
    RecordingConsoleLogger,
)

# ---------------------------------------------------------------------------
# Multi-tool fix scenarios
# ---------------------------------------------------------------------------


def test_run_ai_enhancement_fix_action_noninteractive_applies_safe_then_reviews_risky(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Safe-style fixes auto-apply while risky ones go to review.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
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
            transport=AITransport.API,
            auto_apply=False,
            auto_apply_safe_fixes=True,
        ).model_dump(),
    )

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

    applied_batches: list[list[AIFixSuggestion]] = []
    reviewed_batches: list[list[AIFixSuggestion]] = []

    async def _generate_fixes(
        _issues: object,
        _provider: object,
        _params: object,
    ) -> list[AIFixSuggestion]:
        return [safe_suggestion, risky_suggestion]

    def _apply_fixes(
        candidates: Sequence[AIFixSuggestion],
        **_kwargs: Any,
    ) -> list[AIFixSuggestion]:
        applied_batches.append(list(candidates))
        return [safe_suggestion]

    def _review_interactive(
        candidates: Sequence[AIFixSuggestion],
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[int, int, list[AIFixSuggestion]]:
        reviewed_batches.append(list(candidates))
        return 0, 0, []

    monkeypatch.setattr("lintro.ai.orchestrator.require_ai", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "lintro.ai.orchestrator.get_provider",
        lambda *_a, **_k: MockAIProvider(),
    )
    monkeypatch.setattr(
        "lintro.ai.orchestrator._resolve_issue_path",
        lambda *, file, workspace_root, cwd: Path(file),
    )
    monkeypatch.setattr(
        "lintro.ai.pipeline.generate_fixes_from_params",
        _generate_fixes,
    )
    monkeypatch.setattr("lintro.ai.pipeline.apply_fixes", _apply_fixes)
    monkeypatch.setattr(
        "lintro.ai.pipeline.review_fixes_interactive",
        _review_interactive,
    )
    monkeypatch.setattr("lintro.ai.pipeline.sys.stdin.isatty", lambda: False)

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=RecordingConsoleLogger(),
        output_format="terminal",
    )

    assert_that(applied_batches).is_length(1)
    assert_that([s.code for s in applied_batches[0]]).is_equal_to(["E501"])

    assert_that(reviewed_batches).is_length(1)
    assert_that([s.code for s in reviewed_batches[0]]).is_equal_to(["B101"])


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes_from_params")
@patch("lintro.ai.pipeline.apply_fixes")
@patch("lintro.ai.pipeline.verify_fixes")
@patch(
    "lintro.ai.orchestrator._resolve_issue_path",
    side_effect=lambda *, file, workspace_root, cwd: Path(file),
)
def test_run_ai_enhancement_fix_action_json_auto_applies_safe_style_suggestions(
    _mock_normalize,
    mock_verify_fixes,
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
            transport=AITransport.API,
            max_fix_attempts=5,
            auto_apply=False,
            auto_apply_safe_fixes=True,
        ).model_dump(),
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
    mock_verify_fixes.return_value = ValidationResult()

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
    assert_that(mock_verify_fixes.call_count).is_equal_to(1)
    assert_that(result.metadata).is_not_none()
    assert_that(result.metadata).contains_key("fixed_count")
    assert_that(result.metadata["fixed_count"]).is_equal_to(1)  # type: ignore[index]  # assertpy is_not_none narrows this


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes_from_params")
@patch("lintro.ai.pipeline.apply_fixes")
@patch("lintro.ai.pipeline.verify_fixes")
@patch(
    "lintro.ai.orchestrator._resolve_issue_path",
    side_effect=lambda *, file, workspace_root, cwd: Path(file),
)
def test_run_ai_enhancement_fix_action_json_uses_fresh_rerun_results(
    _mock_normalize,
    mock_verify_fixes,
    mock_apply_fixes,
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """Verify JSON fix action updates result counts via verify_fixes."""
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
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            transport=AITransport.API,
            auto_apply=True,
        ).model_dump(),
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

    ai_result = run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
    )

    # The verification pass is what turns the applied suggestion into the
    # fixed/remaining counts stamped on the result and the AI outcome.
    assert_that(ai_result.fixes_applied).is_equal_to(1)
    assert_that(result.metadata).is_not_none()
    assert_that(result.metadata).contains_key("fixed_count")
    assert_that(result.metadata["fixed_count"]).is_equal_to(1)  # type: ignore[index]  # assertpy is_not_none narrows this


# ---------------------------------------------------------------------------
# TestRerunContext
# ---------------------------------------------------------------------------


def test_rerun_context_absolute_paths_for_context_resolves_targets(tmp_path):
    """Target paths are resolved to absolute form for cwd-explicit rerun."""
    tool_cwd = tmp_path / "tool"
    tool_cwd.mkdir(parents=True)
    inside = tool_cwd / "src" / "main.py"
    inside.parent.mkdir(parents=True)
    inside.write_text("x = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")

    rerun_paths = absolute_paths_for_context(
        file_paths=[str(inside), str(outside)],
    )

    assert_that(rerun_paths[0]).is_equal_to(str(inside.resolve()))
    assert_that(rerun_paths[1]).is_equal_to(str(outside.resolve()))


@patch("lintro.tools.tool_manager.get_tool")
def test_rerun_context_rerun_passes_absolute_paths_without_chdir(
    mock_get_tool,
    tmp_path,
    monkeypatch,
):
    """rerun_tools hands absolute paths to the tool without any os.chdir."""
    import os

    def _fail_chdir(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("rerun_tools must not call os.chdir")

    monkeypatch.setattr(os, "chdir", _fail_chdir)

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
    by_tool: dict[str, tuple[ToolResult, list[BaseIssue]]] = {
        "ruff": (original_result, [issue]),
    }

    captured: dict[str, object] = {}

    class _FakeTool:
        def check(self, paths: Any, options: Any) -> ToolResult:
            captured["cwd"] = os.getcwd()
            captured["paths"] = paths
            return ToolResult(
                name="ruff",
                success=True,
                issues_count=0,
                issues=[],
            )

    mock_get_tool.return_value = _FakeTool()

    before = os.getcwd()
    rerun_results = rerun_tools(by_tool)

    assert_that(rerun_results).is_length(1)
    assert_that(captured.get("cwd")).is_equal_to(before)
    assert_that(captured.get("paths")).is_equal_to([str(source.resolve())])


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

    by_tool: dict[str, tuple[ToolResult, list[BaseIssue]]] = {
        "failing-tool": (result_a, [issue_a]),
        "passing-tool": (result_b, [issue_b]),
    }
    rerun_results = rerun_tools(by_tool)

    assert_that(call_count["failing"]).is_equal_to(1)
    assert_that(call_count["passing"]).is_equal_to(1)
    assert_that(rerun_results).is_not_none()
    assert_that(rerun_results).is_length(1)
    assert_that(rerun_results[0].name).is_equal_to("passing-tool")  # type: ignore[index]  # assertpy is_not_none narrows this
