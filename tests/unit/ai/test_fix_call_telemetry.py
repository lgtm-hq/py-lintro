"""Count observed fix responses at real pipeline/provider boundaries, offline."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIProviderError, AITurnLimitError
from lintro.ai.fix import generate_fixes
from lintro.ai.fix_context import read_file_safely
from lintro.ai.pipeline import run_fix_pipeline
from lintro.ai.providers.response import AIResponse
from lintro.ai.telemetry import AITelemetry
from lintro.ai.validation import ValidationResult
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import MockAIProvider, MockIssue, RecordingConsoleLogger


def _response(content: object, *, measured: bool = True) -> AIResponse:
    """Use odd token totals so splitting a batch would lose measurable usage."""
    return AIResponse(
        content=json.dumps(content),
        model="mock-model",
        input_tokens=31 if measured else 0,
        output_tokens=17 if measured else 0,
        cost_estimate=0.125 if measured else 0.0,
    )


def _provider(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[AIResponse | AIProviderError],
) -> MockAIProvider:
    """Drive the real invoke/retry/fallback seam without an SDK or subprocess."""
    provider = MockAIProvider()
    queued = iter(outcomes)

    async def complete(prompt: str, **kwargs: object) -> AIResponse:
        provider.calls.append({"prompt": prompt, **kwargs})
        outcome = next(queued)
        if isinstance(outcome, AIProviderError):
            raise outcome
        return outcome

    monkeypatch.setattr(provider, "complete", complete)
    return provider


def _usage(telemetry: AITelemetry) -> tuple[int, int, int, float]:
    """Read the public serialized usage fields together for coherent assertions."""
    data = telemetry.to_dict()
    return (
        data["api_calls"],
        data["input_tokens"],
        data["output_tokens"],
        data["cost_usd"],
    )


@pytest.fixture
def config() -> AIConfig:
    """Keep production invocation enabled with retries and applying disabled."""
    return AIConfig(
        enabled=True,
        lint=True,
        review=False,
        transport=AITransport.API,
        max_retries=0,
        rate_limit_max_retries=0,
        auto_apply_safe_fixes=False,
        max_parallel_calls=2,
    )


@pytest.fixture
def telemetry(monkeypatch: pytest.MonkeyPatch) -> AITelemetry:
    """Observe the pipeline's accumulator even when the public call raises."""
    collected = AITelemetry()
    monkeypatch.setattr("lintro.ai.pipeline.AITelemetry", lambda: collected)
    return collected


@pytest.mark.parametrize(
    "kind,expected_calls,measured_calls,suggestion_count",
    [
        ("identical-footprints", 2, 2, 2),
        ("accepted-batch", 1, 1, 2),
        ("rejected-batch", 3, 3, 2),
        ("rejected-single", 1, 1, 0),
        ("unreported-response", 1, 0, 1),
        ("turn-limit", 1, 1, 0),
        ("unreported-limit", 1, 0, 0),
        ("ordinary-error", 0, 0, 0),
    ],
)
async def test_generation_counts_responses_independently_of_surviving_patches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: AIConfig,
    telemetry: AITelemetry,
    kind: str,
    expected_calls: int,
    measured_calls: int,
    suggestion_count: int,
) -> None:
    """Equal footprints, rejected batches and missing usage cannot identify calls."""
    entries = [
        {"original_code": "x = 1", "suggested_code": "x = 2", "line": 1},
        {"original_code": "y = 1", "suggested_code": "y = 2", "line": 2},
    ]
    batch = kind in {"accepted-batch", "rejected-batch"}
    names = (
        ["first.py", "second.py"] if kind == "identical-footprints" else ["first.py"]
    )
    for name in names:
        (tmp_path / name).write_text("x = 1\ny = 1\n")
    issues = [
        MockIssue(file=str(tmp_path / name), line=1, code="B101") for name in names
    ]
    if batch:
        issues.append(MockIssue(file=issues[0].file, line=2, code="B101"))
    outcomes: list[AIResponse | AIProviderError]
    if kind == "turn-limit":
        outcomes = [
            AITurnLimitError(
                "offline limit",
                input_tokens=31,
                output_tokens=17,
                cost_estimate=0.125,
            ),
        ]
    elif kind == "unreported-limit":
        outcomes = [AITurnLimitError("offline limit without usage")]
    elif kind == "ordinary-error":
        outcomes = [AIProviderError("offline without a response")]
    elif batch:
        outcomes = [_response(entries if kind == "accepted-batch" else [])]
        if kind == "rejected-batch":
            outcomes.extend(_response(entry) for entry in entries)
    else:
        outcomes = [
            _response(
                "malformed" if kind == "rejected-single" else entries[0],
                measured=kind != "unreported-response",
            )
            for _ in issues
        ]
    provider = _provider(monkeypatch, outcomes)
    result = ToolResult(
        name="ruff",
        success=False,
        issues=issues,
        issues_count=len(issues),
    )
    budget = CostBudget(max_cost_usd=1.0)
    budget.record(0.25)

    outcome = await run_fix_pipeline(
        fix_issues=[(result, issue) for issue in issues],
        all_results=[result],
        provider=provider,
        ai_config=config,
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=tmp_path,
        budget=budget,
    )

    assert_that(_usage(telemetry)).is_equal_to(
        (
            expected_calls,
            31 * measured_calls,
            17 * measured_calls,
            0.125 * measured_calls,
        ),
    )
    assert_that(result.metadata["ai_metrics"]).is_equal_to(telemetry.to_dict())
    assert_that((budget.spent, budget.reserved)).is_equal_to(
        (0.25 + 0.125 * measured_calls, 0.0),
    )
    assert_that(result.metadata.get("fix_suggestions", [])).is_length(suggestion_count)
    assert_that(outcome).is_equal_to((0, suggestion_count, []))
    assert_that(provider.calls).is_length(len(outcomes))
    assert_that([(tmp_path / name).read_text() for name in names]).is_equal_to(
        ["x = 1\ny = 1\n"] * len(names),
    )


async def test_pipeline_cache_hit_has_no_response_event_or_charge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: AIConfig,
    telemetry: AITelemetry,
) -> None:
    """Populate and reuse a real cache entry while its original usage stays stored."""
    source = tmp_path / "cached.py"
    source.write_text("x = 1\n")
    issue = MockIssue(file=str(source), line=1, code="B101", message="same diagnostic")
    provider = _provider(
        monkeypatch,
        [_response({"original_code": "x = 1", "suggested_code": "x = 2"})],
    )
    seeded = await generate_fixes(
        [issue],
        provider,
        tool_name="ruff",
        ai_config=config,
        workspace_root=tmp_path,
        enable_cache=True,
    )
    result = ToolResult(name="ruff", success=False, issues=[issue], issues_count=1)
    budget = CostBudget()

    await run_fix_pipeline(
        fix_issues=[(result, issue)],
        all_results=[result],
        provider=provider,
        ai_config=config.model_copy(update={"enable_cache": True}),
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=tmp_path,
        budget=budget,
    )

    assert_that(_usage(telemetry)).is_equal_to((0, 0, 0, 0.0))
    assert_that(budget.spent).is_equal_to(0.0)
    assert_that(result.metadata["fix_suggestions"][0]).contains_entry(
        input_tokens=0,
        output_tokens=0,
        cost_estimate=0.0,
        suggested_code=seeded[0].suggested_code,
    )
    assert_that(provider.calls).is_length(1)
    assert_that(source.read_text()).is_equal_to("x = 1\n")


@pytest.mark.parametrize("recovery", ["retry", "fallback"])
async def test_transport_recovery_counts_only_the_returned_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: AIConfig,
    telemetry: AITelemetry,
    recovery: str,
) -> None:
    """A retry/fallback without usage is not an additional measured response."""
    source = tmp_path / "recovery.py"
    source.write_text("x = 1\n")
    issue = MockIssue(file=str(source), line=1, code="B101")
    provider = _provider(
        monkeypatch,
        [AIProviderError("offline transient failure"), _response({})],
    )
    result = ToolResult(name="ruff", success=False, issues=[issue], issues_count=1)
    budget = CostBudget()

    await run_fix_pipeline(
        fix_issues=[(result, issue)],
        all_results=[result],
        provider=provider,
        ai_config=config.model_copy(
            update={
                "max_retries": int(recovery == "retry"),
                "retry_base_delay": 0.1,
                "fallback_models": (
                    ["offline-fallback"] if recovery == "fallback" else []
                ),
            },
        ),
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=tmp_path,
        budget=budget,
    )

    assert_that(_usage(telemetry)).is_equal_to((1, 31, 17, 0.125))
    assert_that((budget.spent, budget.reserved)).is_equal_to((0.125, 0.0))
    assert_that(provider.calls).is_length(2)
    assert_that(provider.calls[-1]["model"]).is_equal_to(
        "offline-fallback" if recovery == "fallback" else "mock-model",
    )
    assert_that(result.metadata["ai_metrics"]).is_equal_to(telemetry.to_dict())
    assert_that(source.read_text()).is_equal_to("x = 1\n")


@pytest.mark.parametrize("exit_type", [RuntimeError, asyncio.CancelledError])
async def test_response_usage_survives_generation_progress_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: AIConfig,
    telemetry: AITelemetry,
    exit_type: type[BaseException],
) -> None:
    """A completed response remains observed when a later progress callback exits."""
    source = tmp_path / "progress.py"
    source.write_text("x = 1\n")
    issue = MockIssue(file=str(source), line=1, code="B101")
    result = ToolResult(name="ruff", success=False, issues=[issue], issues_count=1)
    provider = _provider(monkeypatch, [_response({})])
    error = exit_type("offline progress exit")
    console = RecordingConsoleLogger()

    def progress(text: str, color: str | None = None) -> None:
        if "generating fixes... 1/1" in text:
            raise error

    monkeypatch.setattr(console, "console_output", progress)
    budget = CostBudget()
    with pytest.raises(exit_type) as caught:
        await run_fix_pipeline(
            fix_issues=[(result, issue)],
            all_results=[result],
            provider=provider,
            ai_config=config,
            logger=console,
            output_format="plain",
            workspace_root=tmp_path,
            budget=budget,
        )

    assert_that(caught.value).is_same_as(error)
    assert_that((_usage(telemetry), budget.spent)).is_equal_to(
        ((1, 31, 17, 0.125), 0.125),
    )
    assert_that(source.read_text()).is_equal_to("x = 1\n")


@pytest.mark.parametrize(
    "kind",
    ["accepted", "malformed", "apply-failed", "limit", "error"],
)
@pytest.mark.parametrize("later_exit", [False, True])
async def test_refinement_observes_unapplied_responses_and_retains_usage_on_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: AIConfig,
    telemetry: AITelemetry,
    kind: str,
    later_exit: bool,
) -> None:
    """Actual revert/parse/apply paths cannot discard an observed refinement call."""
    issues = []
    for name in ("first.py", "second.py"):
        source = tmp_path / name
        source.write_text("x = 1\n")
        issues.append(MockIssue(file=str(source), line=1, code="B101"))
    initial = {"original_code": "x = 1", "suggested_code": "x = 2"}
    refined = {
        "original_code": "absent" if kind == "apply-failed" else "x = 1",
        "suggested_code": "x = 3",
    }
    response: AIResponse | AIProviderError
    if kind == "limit":
        response = AITurnLimitError(
            "offline limit",
            input_tokens=31,
            output_tokens=17,
            cost_estimate=0.125,
        )
    elif kind == "error":
        response = AIProviderError("offline without usage")
    else:
        response = _response("malformed" if kind == "malformed" else refined)
    provider = _provider(
        monkeypatch,
        [_response(initial), _response(initial), response, _response({})],
    )
    validation = ValidationResult(
        unverified=2,
        details=[f"[B101] {issue.file}:1 — issue still present" for issue in issues],
    )
    monkeypatch.setattr("lintro.ai.pipeline.verify_fixes", lambda **kw: validation)
    monkeypatch.setattr(
        "lintro.ai.pipeline.validate_applied_fixes",
        lambda fixes: ValidationResult(verified=len(fixes)),
    )
    error = RuntimeError("later refinement read exit")

    def read(path: str) -> str | None:
        if later_exit and path == issues[1].file:
            raise error
        return read_file_safely(path)

    monkeypatch.setattr("lintro.ai.refinement.read_file_safely", read)
    result = ToolResult(name="ruff", success=False, issues=issues, issues_count=2)
    budget = CostBudget(max_cost_usd=1.0)
    budget.record(0.25)
    pipeline = run_fix_pipeline(
        fix_issues=[(result, issue) for issue in issues],
        all_results=[result],
        provider=provider,
        ai_config=config.model_copy(
            update={"auto_apply": True, "max_parallel_calls": 1},
        ),
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=tmp_path,
        budget=budget,
    )
    if later_exit:
        with pytest.raises(RuntimeError) as caught:
            await pipeline
        assert_that(caught.value).is_same_as(error)
    else:
        applied, failed, suggestions = await pipeline
        assert_that((applied, failed, len(suggestions))).is_equal_to((2, 0, 2))
        assert_that(result.metadata["ai_metrics"]).is_equal_to(telemetry.to_dict())

    count = 2 + int(kind != "error") + int(not later_exit)
    assert_that(_usage(telemetry)).is_equal_to(
        (count, 31 * count, 17 * count, 0.125 * count),
    )
    assert_that((budget.spent, budget.remaining, budget.reserved)).is_equal_to(
        (0.25 + 0.125 * count, 0.75 - 0.125 * count, 0.0),
    )
    assert_that([Path(issue.file).read_text() for issue in issues]).is_equal_to(
        ["x = 3\n" if kind == "accepted" else "x = 1\n", "x = 1\n"],
    )
