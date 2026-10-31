"""Budget and progress null-object regressions for issue #3021."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from assertpy import assert_that

from lintro.ai import fix, pipeline, summary
from lintro.ai import invoke as ai_invoke
from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AITurnLimitError
from lintro.ai.fix_params import FixGenParams
from lintro.ai.invoke import call_ai
from lintro.ai.providers.response import AIResponse
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import MockAIProvider, MockIssue, RecordingConsoleLogger


async def test_pipeline_normalizes_a_fresh_budget_for_each_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default pipeline budgets retain session spend without leaking to the next run."""
    budgets: list[CostBudget] = []

    def new_budget() -> CostBudget:
        budget = CostBudget()
        budgets.append(budget)
        return budget

    monkeypatch.setattr(pipeline, "CostBudget", new_budget)
    source = tmp_path / "source.py"
    source.write_text("x = 1\n")
    issue = MockIssue(file=str(source), line=1, code="E501")
    result = ToolResult(name="ruff", success=False, issues=[issue], issues_count=1)
    for _ in range(2):
        provider = MockAIProvider(
            responses=[
                AIResponse(
                    content=json.dumps(
                        {
                            "original_code": "x = 1",
                            "suggested_code": "x = 2",
                            "explanation": "update x",
                            "confidence": "high",
                        },
                    ),
                    model="mock",
                    provider="mock",
                    cost_estimate=0.25,
                ),
            ],
        )
        applied, failed, suggestions = await pipeline.run_fix_pipeline(
            fix_issues=[(result, issue)],
            provider=provider,
            ai_config=AIConfig(enabled=True, transport=AITransport.API, dry_run=True),
            logger=RecordingConsoleLogger(),
            output_format="json",
            workspace_root=tmp_path,
        )
        assert_that((applied, failed)).is_equal_to((0, 0))
        assert_that(suggestions).is_length(1)

    assert_that(budgets).is_length(2)
    assert_that(budgets[0]).is_not_same_as(budgets[1])
    assert_that([budget.spent for budget in budgets]).is_equal_to([0.25, 0.25])
    assert_that([budget.remaining for budget in budgets]).is_equal_to([None, None])


@pytest.mark.parametrize("product", ["fix", "summary", "legacy"])
async def test_standalone_calls_have_independent_unlimited_budgets(
    product: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standalone calls record spend without sharing mutable default budgets."""
    budgets: list[CostBudget] = []
    invoke_product = ai_invoke._call_ai if product == "legacy" else call_ai

    async def invoke(**kwargs: Any) -> AIResponse:
        budget = kwargs["budget"]
        budgets.append(budget)
        return await invoke_product(**kwargs)

    module = {"fix": fix, "summary": summary, "legacy": ai_invoke}[product]
    target = "_call_ai" if product == "legacy" else "call_ai"
    monkeypatch.setattr(module, target, invoke)
    provider = MockAIProvider(
        responses=[
            AIResponse(
                content='{"overview": "done"}',
                model="mock",
                provider="mock",
                cost_estimate=0.25,
            )
            for _ in range(2)
        ],
    )
    config = AIConfig(enabled=True, transport=AITransport.API, max_retries=0)
    for _ in range(2):
        if product == "fix":
            result = await fix._call_fix_ai(
                provider=provider,
                ai_config=config,
                prompt="fix",
                max_tokens=100,
                workspace_root=tmp_path,
            )
            assert_that(result.content).contains("done")
        elif product == "summary":
            summary_result = await summary._call_summary_provider(
                "summarize",
                provider=provider,
                ai_config=config,
                max_tokens=100,
                workspace_root=tmp_path,
            )
            assert_that(summary_result).is_not_none().has_overview("done")
        else:
            response = await call_ai(
                provider=provider,
                ai_config=config,
                user_prompt="legacy caller",
                system_prompt=None,
                budget=None,
            )
            assert_that(response.content).contains("done")

    assert_that(budgets).is_length(2)
    assert_that(budgets[0]).is_not_same_as(budgets[1])
    for budget in budgets:
        assert_that(budget.spent).is_equal_to(0.25)
        assert_that(budget.remaining).is_none()


async def test_unlimited_invocation_keeps_recording_without_blocking() -> None:
    """An uncapped session keeps its spend even after expensive earlier calls."""
    budget = CostBudget()
    budget.record(1000.0)
    provider = MockAIProvider(
        responses=[
            AIResponse(content="ok", model="mock", provider="mock", cost_estimate=0.5)
            for _ in range(2)
        ],
    )
    for _ in range(2):
        response = await call_ai(
            provider=provider,
            ai_config=AIConfig(enabled=True, transport=AITransport.API),
            user_prompt="continue",
            system_prompt=None,
            budget=budget,
        )
        assert_that(response.content).is_equal_to("ok")

    budget.check()
    assert_that(budget.spent).is_equal_to(1001.0)
    assert_that(budget.remaining).is_none()
    assert_that(budget.reserved).is_equal_to(0.0)


@pytest.mark.parametrize("ceiling", [None, 1.0])
async def test_turn_limit_charges_once_and_releases_reservations(
    ceiling: float | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stopped call charges its usage for both unlimited and capped sessions."""
    budget = CostBudget(max_cost_usd=ceiling)
    provider = MockAIProvider()
    monkeypatch.setattr(
        provider,
        "complete",
        AsyncMock(side_effect=AITurnLimitError("limit", cost_estimate=0.25)),
    )
    with pytest.raises(AITurnLimitError, match="limit"):
        await call_ai(
            provider=provider,
            ai_config=AIConfig(enabled=True, transport=AITransport.CLI),
            user_prompt="fix",
            system_prompt=None,
            budget=budget,
        )

    assert_that(budget.spent).is_equal_to(0.25)
    assert_that(budget.reserved).is_equal_to(0.0)


def test_default_progress_is_callable_and_does_nothing(tmp_path: Path) -> None:
    """Parameter bundles provide a callable even when nobody watches progress."""
    params = FixGenParams(workspace_root=tmp_path)
    assert_that(callable(params.progress_callback)).is_true()
    params.progress_callback(1, 2)


@pytest.mark.parametrize("mode", ["single", "batch", "concurrent", "worker-error"])
@pytest.mark.parametrize("watch_progress", [False, True])
async def test_fix_progress_preserves_results_across_completion_paths(
    mode: str,
    watch_progress: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default and custom callbacks work for batches, workers, and worker errors."""
    count = 1 if mode == "single" else 2
    payload = {
        "original_code": "x = 1",
        "suggested_code": "x = 2",
        "explanation": "update x",
        "confidence": "high",
    }
    issues = []
    for index in range(count):
        source = tmp_path / ("batch.py" if mode == "batch" else f"{index}.py")
        source.write_text("x = 1\n" * count)
        issues.append(MockIssue(file=str(source), line=index + 1, code="E501"))

    content = (
        [{**payload, "line": issue.line, "code": issue.code} for issue in issues]
        if mode == "batch"
        else payload
    )
    provider = MockAIProvider(
        responses=[
            AIResponse(content=json.dumps(content), model="mock", provider="mock")
            for _ in range(count)
        ],
    )
    if mode == "worker-error":
        original = fix._generate_single_fix

        async def fail_one(issue: MockIssue, *args: Any, **kwargs: Any) -> Any:
            if issue.line == 2:
                raise RuntimeError("worker failed")
            return await original(issue, *args, **kwargs)

        monkeypatch.setattr(fix, "_generate_single_fix", fail_one)

    progress: list[tuple[int, int]] = []

    def report(completed: int, total: int) -> None:
        progress.append((completed, total))

    params = FixGenParams(workspace_root=tmp_path, tool_name="ruff")
    if watch_progress:
        suggestions = await fix.generate_fixes(
            issues,
            provider,
            tool_name="ruff",
            workspace_root=tmp_path,
            progress_callback=report,
        )
    else:
        suggestions = await fix.generate_fixes_from_params(issues, provider, params)

    expected_count = 1 if mode == "worker-error" else count
    assert_that(suggestions).is_length(expected_count)
    assert_that([s.suggested_code for s in suggestions]).contains_only("x = 2")
    expected_progress = (
        [(count, count)] if mode == "batch" else [(1, count), (2, count)][:count]
    )
    assert_that(progress).is_equal_to(expected_progress if watch_progress else [])
