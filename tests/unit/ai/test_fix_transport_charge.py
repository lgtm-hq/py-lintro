"""Turn-limited fix calls retain the spend observed by the public transport."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import (
    AICostBudgetExceededError,
    AIProviderError,
    AITurnLimitError,
)
from lintro.ai.fix import generate_fixes
from lintro.ai.fix_context import read_file_safely
from lintro.ai.invoke import call_ai
from lintro.ai.models import AIFixSuggestion
from lintro.ai.pipeline import run_fix_pipeline
from lintro.ai.providers.response import AIResponse
from lintro.ai.refinement import refine_unverified_fixes
from lintro.ai.validation import ValidationResult
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import MockAIProvider, MockIssue, RecordingConsoleLogger


class _OfflineProvider(MockAIProvider):
    """Replay responses or billed failures without any SDK or subprocess calls."""

    def __init__(self, *outcomes: AIResponse | AIProviderError) -> None:
        """Queue exactly the outcomes the boundary is allowed to consume."""
        super().__init__()
        self.outcomes = list(outcomes)
        self.attempts = 0

    async def complete(self, *_args: object, **_kwargs: object) -> AIResponse:
        """Return or raise the next offline outcome."""
        self.attempts += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, AIProviderError):
            raise outcome
        return outcome


@pytest.fixture
def config() -> AIConfig:
    """Use the CLI turn-limit path with no transient retries."""
    return AIConfig(
        enabled=True,
        lint=True,
        review=False,
        transport=AITransport.CLI,
        max_retries=0,
        rate_limit_max_retries=0,
        auto_apply_safe_fixes=False,
    )


@pytest.mark.parametrize("ceiling", [None, 0.125])
async def test_public_invocation_preserves_billed_exception_and_budget_cap(
    config: AIConfig,
    ceiling: float | None,
) -> None:
    """The same exception escapes once, with reservations released before reuse."""
    failure = AITurnLimitError(
        "offline turn limit",
        input_tokens=31,
        output_tokens=17,
        cost_estimate=0.25,
        turns=4,
    )
    provider = _OfflineProvider(failure)
    budget = CostBudget(max_cost_usd=ceiling)
    retry_config = config.model_copy(
        update={"max_retries": 2, "fallback_models": ["offline-fallback"]},
    )

    with pytest.raises(AITurnLimitError) as caught:
        await call_ai(
            provider=provider,
            ai_config=retry_config,
            user_prompt="offline",
            system_prompt=None,
            budget=budget,
        )

    assert_that(caught.value).is_same_as(failure)
    assert_that(str(caught.value)).is_equal_to("offline turn limit")
    assert_that(
        (failure.input_tokens, failure.output_tokens, failure.turns),
    ).is_equal_to(
        (31, 17, 4),
    )
    assert_that((budget.spent, budget.reserved)).is_equal_to((0.25, 0.0))
    if ceiling is not None:
        with pytest.raises(AICostBudgetExceededError):
            await call_ai(
                provider=provider,
                ai_config=config,
                user_prompt="blocked before provider",
                system_prompt=None,
                budget=budget,
            )
        assert_that(budget.remaining).is_equal_to(0.0)
        assert_that(budget.spent).is_equal_to(0.25)
    assert_that(provider.attempts).is_equal_to(1)


@pytest.mark.parametrize(
    "failure,cost",
    [
        pytest.param(
            AITurnLimitError("offline billed limit", cost_estimate=0.125),
            0.125,
            id="billed-limit",
        ),
        pytest.param(AITurnLimitError("offline unreported limit"), 0.0, id="unknown"),
        pytest.param(AIProviderError("offline failure"), 0.0, id="ordinary-error"),
    ],
)
async def test_generation_session_retains_only_observed_exception_cost(
    tmp_path: Path,
    config: AIConfig,
    failure: AIProviderError,
    cost: float,
) -> None:
    """A caught transport failure adds its known spend without creating a fix."""
    source = tmp_path / "generation.py"
    source.write_text("x = 1\n")
    issue = MockIssue(file=str(source), line=1, code="B101")
    result = ToolResult(name="ruff", success=False, issues=[issue], issues_count=1)
    budget = CostBudget(max_cost_usd=1.0)
    budget.record(0.25)
    provider = _OfflineProvider(failure)

    outcome = await run_fix_pipeline(
        fix_issues=[(result, issue)],
        all_results=[result],
        provider=provider,
        ai_config=config,
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=tmp_path,
        budget=budget,
    )

    assert_that((budget.spent, budget.remaining, budget.reserved)).is_equal_to(
        (0.25 + cost, 0.75 - cost, 0.0),
    )
    assert_that(outcome).is_equal_to((0, 0, []))
    assert_that(source.read_text()).is_equal_to("x = 1\n")
    assert_that(provider.attempts).is_equal_to(1)


@pytest.mark.parametrize("workers", [1, 2])
async def test_turn_limited_batch_and_single_fallback_charges_reach_callback_once(
    tmp_path: Path,
    config: AIConfig,
    workers: int,
) -> None:
    """A billed batch failure and its successful fallbacks each publish one charge."""
    source = tmp_path / "batch.py"
    source.write_text("x = 1\ny = 2\n")
    provider = _OfflineProvider(
        AITurnLimitError("offline batch limit", cost_estimate=0.125),
        AIResponse(
            content=json.dumps({"original_code": "x = 1", "suggested_code": "x = 3"}),
            model="mock-model",
            cost_estimate=0.25,
        ),
        AIResponse(
            content=json.dumps({"original_code": "y = 2", "suggested_code": "y = 4"}),
            model="mock-model",
            cost_estimate=0.5,
        ),
    )
    charges: list[float] = []

    suggestions = await generate_fixes(
        [MockIssue(file=str(source), line=line, code="B101") for line in (1, 2)],
        provider,
        tool_name="ruff",
        ai_config=config,
        workspace_root=tmp_path,
        max_workers=workers,
        cost_callback=charges.append,
    )

    assert_that(charges).is_equal_to([0.125, 0.25, 0.5])
    assert_that([suggestion.cost_estimate for suggestion in suggestions]).is_equal_to(
        [0.25, 0.5],
    )
    assert_that(source.read_text()).is_equal_to("x = 1\ny = 2\n")
    assert_that(provider.attempts).is_equal_to(3)


@pytest.mark.parametrize("cost", [0.125, 0.0])
@pytest.mark.parametrize("with_callback", [True, False])
async def test_refinement_returns_turn_limit_spend_with_existing_revert_behavior(
    tmp_path: Path,
    config: AIConfig,
    cost: float,
    with_callback: bool,
) -> None:
    """Totals and optional callbacks each receive the charge once after a revert."""
    source = tmp_path / "refinement.py"
    source.write_text("x = 2\n")
    suggestion = AIFixSuggestion(
        file=source.name,
        line=1,
        code="B101",
        original_code="x = 1",
        suggested_code="x = 2",
    )
    charges: list[float] = []

    refined, total = await refine_unverified_fixes(
        applied_suggestions=[suggestion],
        validation=ValidationResult(
            unverified=1,
            details=["[B101] refinement.py:1 — issue still present"],
        ),
        provider=_OfflineProvider(
            AITurnLimitError("offline limit", cost_estimate=cost),
        ),
        ai_config=config,
        workspace_root=tmp_path,
        cost_callback=charges.append if with_callback else None,
    )

    assert_that(total).is_equal_to(cost)
    assert_that(charges).is_equal_to([cost] if with_callback else [])
    assert_that(refined).is_empty()
    assert_that(source.read_text()).is_equal_to("x = 1\n")
    assert_that((suggestion.original_code, suggestion.suggested_code)).is_equal_to(
        ("x = 1", "x = 2"),
    )


@pytest.mark.parametrize("later_read_fails", [False, True])
@pytest.mark.parametrize(
    "failure,cost",
    [
        pytest.param(
            AITurnLimitError("offline refinement limit", cost_estimate=0.125),
            0.125,
            id="billed-limit",
        ),
        pytest.param(AITurnLimitError("offline unreported limit"), 0.0, id="unknown"),
        pytest.param(AIProviderError("offline failure"), 0.0, id="ordinary-error"),
    ],
)
async def test_refinement_turn_limit_settles_session_before_return_or_later_exit(
    tmp_path: Path,
    config: AIConfig,
    monkeypatch: pytest.MonkeyPatch,
    failure: AIProviderError,
    cost: float,
    later_read_fails: bool,
) -> None:
    """Real refinement charges survive normal return and a later boundary error."""
    issues = []
    for name in ("first.py", "second.py"):
        source = tmp_path / name
        source.write_text("x = 1\n")
        issues.append(MockIssue(file=str(source), line=1, code="B101"))
    result = ToolResult(name="ruff", success=False, issues=issues, issues_count=2)
    initial = AIResponse(
        content=json.dumps({"original_code": "x = 1", "suggested_code": "x = 2"}),
        model="mock-model",
        cost_estimate=0.0625,
    )
    provider = _OfflineProvider(
        initial,
        initial,
        failure,
        AIResponse(
            content=json.dumps({"original_code": "x = 1", "suggested_code": "x = 3"}),
            model="mock-model",
        ),
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
    error = RuntimeError("later refinement read stopped")

    # Patch refinement's imported reader to inject an uncaught boundary exit,
    # unlike the caught provider error above. The real reader converts
    # OSError/UnicodeDecodeError to None; other reads still delegate to it.
    def read_refinement_file(path: str) -> str | None:
        # Reads happen after reverting and outside the per-suggestion catch.
        if later_read_fails and path == issues[1].file:
            raise error
        return read_file_safely(path)

    monkeypatch.setattr("lintro.ai.refinement.read_file_safely", read_refinement_file)
    budget = CostBudget(max_cost_usd=0.5)
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

    if later_read_fails:
        with pytest.raises(RuntimeError) as raised:
            await pipeline
        assert_that(raised.value).is_same_as(error)
    else:
        applied, failed, suggestions = await pipeline
        assert_that((applied, failed, len(suggestions))).is_equal_to((2, 0, 2))

    assert_that((budget.spent, budget.remaining, budget.reserved)).is_equal_to(
        (0.375 + cost, 0.125 - cost, 0.0),
    )
    if cost:
        with pytest.raises(AICostBudgetExceededError):
            budget.check()
    else:
        budget.check()
    assert_that([Path(issue.file).read_text() for issue in issues]).is_equal_to(
        ["x = 1\n", "x = 1\n" if later_read_fails else "x = 3\n"],
    )
    assert_that(provider.attempts).is_equal_to(3 if later_read_fails else 4)


async def test_turn_limit_charge_exhausts_session_before_next_tool(
    tmp_path: Path,
    config: AIConfig,
) -> None:
    """The next tool's budget check sees the first tool's billed exception."""
    source = tmp_path / "cap.py"
    source.write_text("x = 1\n")
    issue = MockIssue(file=str(source), line=1, code="B101")
    results = [
        ToolResult(name=name, success=False, issues=[issue], issues_count=1)
        for name in ("ruff", "bandit")
    ]
    provider = _OfflineProvider(AITurnLimitError("offline limit", cost_estimate=0.125))
    budget = CostBudget(max_cost_usd=0.125)

    with pytest.raises(AICostBudgetExceededError):
        await run_fix_pipeline(
            fix_issues=[(result, issue) for result in results],
            all_results=results,
            provider=provider,
            ai_config=config,
            logger=RecordingConsoleLogger(),
            output_format="json",
            workspace_root=tmp_path,
            budget=budget,
        )

    assert_that((budget.spent, budget.remaining, budget.reserved)).is_equal_to(
        (0.125, 0.0, 0.0),
    )
    assert_that(results[1].metadata).is_empty()
    assert_that(source.read_text()).is_equal_to("x = 1\n")
    assert_that(provider.attempts).is_equal_to(1)
