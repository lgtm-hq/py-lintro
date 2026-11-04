"""Completed fix responses retain their charges even without usable patches."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AICostBudgetExceededError, AIProviderError
from lintro.ai.fix import generate_fixes
from lintro.ai.models import AIFixSuggestion
from lintro.ai.pipeline import run_fix_pipeline
from lintro.ai.providers.response import AIResponse
from lintro.ai.refinement import refine_unverified_fixes
from lintro.ai.validation import ValidationResult
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from tests.unit.ai.conftest import MockAIProvider, MockIssue, RecordingConsoleLogger

_FIX = {"original_code": "x = 1", "suggested_code": "x = 3"}
_RESPONSES = [
    pytest.param("private-malformed-payload", False, id="malformed-json"),
    pytest.param(
        json.dumps({**_FIX, "suggested_code": ["private-semantic-payload"]}),
        False,
        id="semantic-invalid",
    ),
    pytest.param(
        json.dumps({**_FIX, "suggested_code": "x = 1"}),
        False,
        id="noop",
    ),
    pytest.param(json.dumps({**_FIX, "suggested_code": ""}), False, id="empty"),
    pytest.param(json.dumps(_FIX), True, id="accepted"),
    pytest.param(None, False, id="provider-exception"),
]


@pytest.fixture
def ai_config() -> AIConfig:
    """Disable retries so offline exceptions represent exactly one failed call."""
    return AIConfig(
        enabled=True,
        lint=True,
        review=False,
        transport=AITransport.API,
        max_retries=0,
        rate_limit_max_retries=0,
    )


def _response(content: str, cost: float) -> AIResponse:
    """Supply offline usage and cost independently of the payload's validity."""
    return AIResponse(
        content=content,
        model="mock",
        provider="mock",
        input_tokens=31,
        output_tokens=17,
        cost_estimate=cost,
    )


@pytest.mark.parametrize("enable_cache", [True, False], ids=["cache-hit", "uncached"])
async def test_generation_cache_reuse_charges_only_new_responses(
    tmp_path: Path,
    ai_config: AIConfig,
    enable_cache: bool,
) -> None:
    """A real cache round trip skips charges; bypassing it charges once."""
    source = tmp_path / "cached.py"
    source.write_bytes(b"# unchanged context\nx = 1\n")
    issue = MockIssue(file=str(source), line=2, code="B101", message="Issue")
    provider = MockAIProvider(
        responses=[
            _response(json.dumps(_FIX), 0.125),
            _response(json.dumps(_FIX), 0.25),
        ],
    )
    initial_costs: list[float] = []

    initial = await generate_fixes(
        [issue],
        provider,
        tool_name="ruff",
        ai_config=ai_config,
        workspace_root=tmp_path,
        enable_cache=True,
        cost_callback=initial_costs.append,
    )

    assert_that(initial).is_length(1)
    assert_that(initial_costs).is_equal_to([0.125])
    assert_that(initial[0].cost_estimate).is_equal_to(0.125)
    cache_files = list((tmp_path / ".lintro-cache/ai").glob("*.json"))
    assert_that(cache_files).is_length(1)
    assert_that(json.loads(cache_files[0].read_text())["suggestion"]).contains_entry(
        cost_estimate=0.125,
        input_tokens=31,
        output_tokens=17,
    )
    response_costs: list[float] = []

    repeated = await generate_fixes(
        [issue],
        provider,
        tool_name="ruff",
        ai_config=ai_config,
        workspace_root=tmp_path,
        enable_cache=enable_cache,
        cost_callback=response_costs.append,
    )

    assert_that(repeated).is_length(1)
    assert_that(response_costs).is_equal_to([] if enable_cache else [0.25])
    assert_that(provider.calls).is_length(1 if enable_cache else 2)
    assert_that(
        (
            repeated[0].input_tokens,
            repeated[0].output_tokens,
            repeated[0].cost_estimate,
        ),
    ).is_equal_to((0, 0, 0.0) if enable_cache else (31, 17, 0.25))
    assert_that(
        (repeated[0].original_code, repeated[0].suggested_code, repeated[0].diff),
    ).is_equal_to(("x = 1", "x = 3", initial[0].diff))
    assert_that(source.read_bytes()).is_equal_to(b"# unchanged context\nx = 1\n")


@pytest.mark.parametrize("content,accepted", _RESPONSES)
async def test_generation_publishes_completed_response_cost(
    tmp_path: Path,
    ai_config: AIConfig,
    monkeypatch: pytest.MonkeyPatch,
    content: str | None,
    accepted: bool,
) -> None:
    """Public pipeline budget, telemetry and audit include rejected responses."""
    source = tmp_path / "generation.py"
    source.write_bytes(b"# unchanged context\nx = 1\n")
    issue = MockIssue(file=str(source), line=2, code="B101", message="Issue")
    result = ToolResult(name="ruff", success=False, issues=[issue], issues_count=1)
    provider = MockAIProvider()
    completion = (
        _response(content, 0.125) if content is not None else AIProviderError("offline")
    )
    monkeypatch.setattr(provider, "complete", AsyncMock(side_effect=[completion]))
    budget = CostBudget(max_cost_usd=1.0)
    budget.record(0.25)
    messages: list[str] = []
    logger.add(messages.append, format="{message}", level="DEBUG")
    config = ai_config.model_copy(update={"auto_apply_safe_fixes": False})

    applied, failed, suggestions = await run_fix_pipeline(
        fix_issues=[(result, issue)],
        all_results=[result],
        provider=provider,
        ai_config=config,
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=tmp_path,
        budget=budget,
    )

    expected_cost = 0.125 if content is not None else 0.0
    assert_that(budget.spent).is_equal_to(0.25 + expected_cost)
    assert_that(budget.remaining).is_equal_to(0.75 - expected_cost)
    assert_that(budget.reserved).is_equal_to(0.0)
    assert_that(result.metadata["ai_metrics"]["cost_usd"]).is_equal_to(expected_cost)
    audit_text = (tmp_path / ".lintro-cache/ai/audit.jsonl").read_text()
    assert_that(json.loads(audit_text)["total_cost_usd"]).is_equal_to(expected_cost)
    assert_that(audit_text + "".join(messages)).does_not_contain(
        "private-malformed-payload",
        "private-semantic-payload",
    )
    assert_that((applied, failed, suggestions)).is_equal_to((0, int(accepted), []))
    assert_that(source.read_bytes()).is_equal_to(b"# unchanged context\nx = 1\n")
    payloads = result.metadata.get("fix_suggestions", [])
    assert_that(payloads).is_length(int(accepted))
    if accepted:
        assert_that(payloads[0]).contains_entry(
            input_tokens=31,
            output_tokens=17,
            cost_estimate=0.125,
            original_code="x = 1",
            suggested_code="x = 3",
        )


@pytest.mark.parametrize("content,accepted", _RESPONSES)
async def test_refinement_charges_before_parsing_without_changing_rollback(
    tmp_path: Path,
    ai_config: AIConfig,
    monkeypatch: pytest.MonkeyPatch,
    content: str | None,
    accepted: bool,
) -> None:
    """Real revert/parser/apply paths keep rejected attempts reverted and charged."""
    source = tmp_path / "refinement.py"
    source.write_bytes(b"# unchanged context\nx = 2\n")
    original = AIFixSuggestion(
        file="refinement.py",
        line=2,
        code="B101",
        tool_name="ruff",
        original_code="x = 1",
        suggested_code="x = 2",
    )
    provider = MockAIProvider()
    completion = (
        _response(content, 0.125) if content is not None else AIProviderError("offline")
    )
    monkeypatch.setattr(provider, "complete", AsyncMock(side_effect=[completion]))

    refined, cost = await refine_unverified_fixes(
        applied_suggestions=[original],
        validation=ValidationResult(
            unverified=1,
            details=["[B101] refinement.py:2 — issue still present"],
        ),
        provider=provider,
        ai_config=ai_config,
        workspace_root=tmp_path,
    )

    assert_that(cost).is_equal_to(0.125 if content is not None else 0.0)
    assert_that(refined).is_length(int(accepted))
    expected = (
        b"# unchanged context\nx = 3\n" if accepted else b"# unchanged context\nx = 1\n"
    )
    assert_that(source.read_bytes()).is_equal_to(expected)
    assert_that((original.original_code, original.suggested_code)).is_equal_to(
        ("x = 1", "x = 2"),
    )
    if accepted:
        assert_that((refined[0].input_tokens, refined[0].output_tokens)).is_equal_to(
            (31, 17),
        )
        assert_that(refined[0].cost_estimate).is_equal_to(0.125)
        assert_that(refined[0].diff).contains("-x = 1", "+x = 3")


@pytest.mark.parametrize(
    "batch_kind",
    ["accepted", "malformed", "noop", "count-mismatch"],
)
async def test_generation_batch_and_fallback_costs_are_counted_once(
    tmp_path: Path,
    ai_config: AIConfig,
    batch_kind: str,
) -> None:
    """Charge a discarded batch plus both fallback replies without inflating fixes."""
    source = tmp_path / "batch.py"
    source.write_text("x = 1\ny = 2\n")
    entries = [
        {**_FIX, "line": 1, "code": "B101"},
        {
            "original_code": "y = 2",
            "suggested_code": "y = 4",
            "line": 2,
            "code": "B101",
        },
    ]
    batch = {
        "accepted": json.dumps(entries),
        "malformed": "private-batch-payload",
        "noop": "[]",
        "count-mismatch": json.dumps(entries[:1]),
    }[batch_kind]
    responses = [_response(batch, 0.125)]
    if batch_kind != "accepted":
        responses.extend(
            _response(json.dumps(entry), cost)
            for entry, cost in zip(entries, [0.25, 0.5], strict=True)
        )
    issues = [MockIssue(file=str(source), line=line, code="B101") for line in (1, 2)]
    result = ToolResult(name="ruff", success=False, issues=issues, issues_count=2)
    budget = CostBudget()

    await run_fix_pipeline(
        fix_issues=[(result, issue) for issue in issues],
        all_results=[result],
        provider=MockAIProvider(responses=responses),
        ai_config=ai_config.model_copy(
            update={"max_parallel_calls": 1, "auto_apply_safe_fixes": False},
        ),
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=tmp_path,
        budget=budget,
    )

    assert_that(budget.spent).is_equal_to(
        sum(response.cost_estimate for response in responses),
    )
    assert_that(result.metadata["ai_metrics"]["cost_usd"]).is_equal_to(budget.spent)
    fixes = result.metadata["fix_suggestions"]
    assert_that([fix["suggested_code"] for fix in fixes]).is_equal_to(
        ["x = 3", "y = 4"],
    )
    expected_costs = [0.0625, 0.0625] if batch_kind == "accepted" else [0.25, 0.5]
    assert_that([fix["cost_estimate"] for fix in fixes]).is_equal_to(expected_costs)
    expected_tokens = [(15, 8)] * 2 if batch_kind == "accepted" else [(31, 17)] * 2
    assert_that(
        [(fix["input_tokens"], fix["output_tokens"]) for fix in fixes],
    ).is_equal_to(expected_tokens)
    assert_that(source.read_text()).is_equal_to("x = 1\ny = 2\n")


@pytest.mark.parametrize("workers", [1, 3])
async def test_generation_mixed_responses_keep_only_returned_charges(
    tmp_path: Path,
    ai_config: AIConfig,
    monkeypatch: pytest.MonkeyPatch,
    workers: int,
) -> None:
    """Sequential and concurrent generation keep a valid fix and a rejected charge."""
    issues = []
    for name in ("accepted", "rejected", "exception"):
        source = tmp_path / f"{name}.py"
        source.write_text("x = 1\n")
        issues.append(MockIssue(file=str(source), line=1, code="B101"))
    result = ToolResult(name="ruff", success=False, issues=issues, issues_count=3)
    provider = MockAIProvider()
    monkeypatch.setattr(
        provider,
        "complete",
        AsyncMock(
            side_effect=[
                _response(json.dumps(_FIX), 0.125),
                _response("malformed", 0.25),
                AIProviderError("offline"),
            ],
        ),
    )
    budget = CostBudget()

    await run_fix_pipeline(
        fix_issues=[(result, issue) for issue in issues],
        all_results=[result],
        provider=provider,
        ai_config=ai_config.model_copy(
            update={"max_parallel_calls": workers, "auto_apply_safe_fixes": False},
        ),
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=tmp_path,
        budget=budget,
    )

    assert_that(budget.spent).is_equal_to(0.375)
    assert_that(result.metadata["ai_metrics"]["cost_usd"]).is_equal_to(0.375)
    assert_that(result.metadata["fix_suggestions"]).is_length(1)
    assert_that(result.metadata["fix_suggestions"][0]["cost_estimate"]).is_equal_to(
        0.125,
    )
    assert_that([Path(issue.file).read_text() for issue in issues]).is_equal_to(
        ["x = 1\n"] * 3,
    )


@pytest.mark.parametrize("kind", ["malformed", "noop", "mixed"])
async def test_pipeline_retains_refinement_cost_even_without_applied_refinements(
    tmp_path: Path,
    ai_config: AIConfig,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    """The caller includes every refinement charge in session spend and audit."""
    count = 3 if kind == "mixed" else 1
    issues = []
    for index in range(count):
        source = tmp_path / f"source{index}.py"
        source.write_text("x = 1\n")
        issues.append(MockIssue(file=str(source), line=1, code="B101"))
    result = ToolResult(name="ruff", success=False, issues=issues, issues_count=count)
    initial = json.dumps({**_FIX, "suggested_code": "x = 2"})
    noop = json.dumps({**_FIX, "suggested_code": "x = 1"})
    completions: list[AIResponse | AIProviderError] = [
        _response(initial, 0.125) for _ in issues
    ]
    completions.append(_response(noop if kind == "noop" else "malformed", 0.25))
    if kind == "mixed":
        completions.extend(
            [_response(json.dumps(_FIX), 0.5), AIProviderError("offline")],
        )
    provider = MockAIProvider()
    monkeypatch.setattr(provider, "complete", AsyncMock(side_effect=completions))
    validation = ValidationResult(
        unverified=count,
        unverified_by_tool={"ruff": count},
        details=[f"[B101] {issue.file}:1 — issue still present" for issue in issues],
    )
    monkeypatch.setattr("lintro.ai.pipeline.verify_fixes", lambda **kwargs: validation)
    monkeypatch.setattr(
        "lintro.ai.pipeline.validate_applied_fixes",
        lambda fixes: ValidationResult(
            verified=len(fixes),
            verified_by_tool={"ruff": len(fixes)},
        ),
    )
    budget = CostBudget()

    applied, failed, suggestions = await run_fix_pipeline(
        fix_issues=[(result, issue) for issue in issues],
        all_results=[result],
        provider=provider,
        ai_config=ai_config.model_copy(
            update={"auto_apply": True, "max_parallel_calls": 1},
        ),
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=tmp_path,
        budget=budget,
    )

    expected_cost = count * 0.125 + (0.75 if kind == "mixed" else 0.25)
    assert_that(budget.spent).is_equal_to(expected_cost)
    assert_that(result.metadata["ai_metrics"]["cost_usd"]).is_equal_to(expected_cost)
    audit = json.loads((tmp_path / ".lintro-cache/ai/audit.jsonl").read_text())
    assert_that(audit["total_cost_usd"]).is_equal_to(expected_cost)
    assert_that((applied, failed, len(suggestions))).is_equal_to((count, 0, count))
    expected_sources = (
        ["x = 1\n", "x = 3\n", "x = 1\n"] if kind == "mixed" else ["x = 1\n"]
    )
    assert_that([Path(issue.file).read_text() for issue in issues]).is_equal_to(
        expected_sources,
    )
    assert_that(validation.verified).is_equal_to(int(kind == "mixed"))
    assert_that(validation.unverified).is_equal_to(count - int(kind == "mixed"))


async def test_rejected_generation_cost_reaches_existing_next_tool_budget_gate(
    tmp_path: Path,
    ai_config: AIConfig,
) -> None:
    """A completed rejected reply exhausts the budget before the next tool starts."""
    pairs: list[tuple[ToolResult, BaseIssue]] = []
    for name in ("ruff", "bandit"):
        source = tmp_path / f"{name}.py"
        source.write_text("x = 1\n")
        issue = MockIssue(file=str(source), line=1, code="B101")
        pairs.append(
            (
                ToolResult(name=name, success=False, issues=[issue], issues_count=1),
                issue,
            ),
        )
    budget = CostBudget(max_cost_usd=0.125)

    with pytest.raises(AICostBudgetExceededError):
        await run_fix_pipeline(
            fix_issues=pairs,
            all_results=[result for result, _ in pairs],
            provider=MockAIProvider(responses=[_response("malformed", 0.125)]),
            ai_config=ai_config,
            logger=RecordingConsoleLogger(),
            output_format="json",
            workspace_root=tmp_path,
            budget=budget,
        )

    assert_that((budget.spent, budget.remaining, budget.reserved)).is_equal_to(
        (0.125, 0.0, 0.0),
    )
    assert_that(pairs[1][0].metadata).is_empty()
    assert_that([Path(issue.file).read_text() for _, issue in pairs]).is_equal_to(
        ["x = 1\n"] * 2,
    )


async def test_unreported_zero_cost_is_not_repriced_by_fix_accounting(
    tmp_path: Path,
    ai_config: AIConfig,
) -> None:
    """Unknown provider measurements remain unchanged on a valid suggestion."""
    source = tmp_path / "unreported.py"
    source.write_text("x = 1\n")
    response = AIResponse(content=json.dumps(_FIX), model="mock", provider="mock")
    issue = MockIssue(file=str(source), line=1, code="B101")
    result = ToolResult(name="ruff", success=False, issues=[issue], issues_count=1)
    budget = CostBudget()

    await run_fix_pipeline(
        fix_issues=[(result, issue)],
        all_results=[result],
        provider=MockAIProvider(responses=[response]),
        ai_config=ai_config.model_copy(update={"auto_apply_safe_fixes": False}),
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=tmp_path,
        budget=budget,
    )

    assert_that(budget.spent).is_equal_to(0.0)
    assert_that(result.metadata["fix_suggestions"][0]).contains_entry(
        input_tokens=0,
        output_tokens=0,
        cost_estimate=0.0,
    )
    assert_that(
        (response.turns, response.reported_models, response.cost_reported),
    ).is_equal_to((None, (), False))
    assert_that(source.read_text()).is_equal_to("x = 1\n")
