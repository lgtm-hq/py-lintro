"""Suggestion estimates at real display and interactive output boundaries."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from assertpy import assert_that
from rich.console import Console
from rich.text import Text

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.cost import format_cost
from lintro.ai.display import render_fixes
from lintro.ai.enums import AITransport, ConfidenceLevel
from lintro.ai.fix import generate_fixes
from lintro.ai.interactive import review_fixes_interactive
from lintro.ai.models import AIFixSuggestion
from lintro.ai.pipeline import run_fix_pipeline
from lintro.ai.providers.response import AIResponse
from lintro.ai.validation import ValidationResult
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import MockAIProvider, MockIssue, RecordingConsoleLogger


def _responses(kind: str) -> list[AIResponse]:
    """Supply real parsing/filtering/fallback paths with distinct spend."""
    first = {
        "original_code": "x = 1",
        "suggested_code": "x = 2",
        "confidence": "high",
    }
    second = {
        "original_code": "y = 1",
        "suggested_code": "y = 2",
        "confidence": "low" if kind == "discarded" else "high",
    }
    contents: list[object] = [first]
    if kind == "batch-fallback":
        contents = [[], first, second]
    elif kind == "discarded":
        contents.append(second)
    elif kind == "refinement":
        contents.append("malformed refinement")
    elif kind == "empty":
        contents = [{"original_code": "", "suggested_code": ""}]
    cost = 0.0 if kind == "unknown" else 0.125
    return [
        AIResponse(
            content=json.dumps(content),
            model="mock-model",
            input_tokens=0 if kind == "unknown" else 31,
            output_tokens=0 if kind == "unknown" else 17,
            cost_estimate=0.25 if index and kind == "refinement" else cost,
        )
        for index, content in enumerate(contents)
    ]


@pytest.fixture(
    params=[
        ("ordinary", 1, 0.125, 0.125),
        ("discarded", 1, 0.125, 0.25),
        ("batch-fallback", 2, 0.25, 0.375),
        ("refinement", 1, 0.125, 0.375),
        ("empty", 0, 0.0, 0.125),
        ("cache", 1, 0.0, 0.0),
        ("unknown", 1, 0.0, 0.0),
    ],
    ids=lambda case: case[0],
)
async def cost_case(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[AIFixSuggestion], float]:
    """Run production generation and settlement before testing visible totals."""
    kind, count, suggestion_cost, session_cost = request.param
    source = tmp_path / "first.py"
    source.write_text("x = 1\ny = 1\n")
    issues = [MockIssue(file=str(source), line=1, code="B101")]
    if kind in {"discarded", "batch-fallback"}:
        other = tmp_path / "second.py" if kind == "discarded" else source
        other.write_text(source.read_text())
        issues.append(MockIssue(file=str(other), line=2, code="B101"))
    config = AIConfig(
        enabled=True,
        transport=AITransport.API,
        max_retries=0,
        max_parallel_calls=1,
        min_confidence=ConfidenceLevel.HIGH,
        dry_run=kind != "refinement",
        auto_apply=kind == "refinement",
        auto_apply_safe_fixes=False,
        enable_cache=kind == "cache",
    )
    provider = MockAIProvider(responses=_responses(kind))
    if kind == "cache":
        await generate_fixes(
            issues,
            provider,
            tool_name="ruff",
            ai_config=config,
            workspace_root=tmp_path,
            enable_cache=True,
        )
    if kind == "refinement":
        validation = ValidationResult(
            unverified=1,
            details=[f"[B101] {source}:1 — issue still present"],
        )
        monkeypatch.setattr("lintro.ai.pipeline.verify_fixes", lambda **kw: validation)
    result = ToolResult(
        name="ruff",
        success=False,
        issues=issues,
        issues_count=len(issues),
    )
    budget = CostBudget(max_cost_usd=1.0)
    _, _, suggestions = await run_fix_pipeline(
        fix_issues=[(result, issue) for issue in issues],
        all_results=[result],
        provider=provider,
        ai_config=config,
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=tmp_path,
        budget=budget,
    )
    assert_that(suggestions).is_length(count)
    assert_that(sum(s.cost_estimate for s in suggestions)).is_equal_to(suggestion_cost)
    assert_that((budget.spent, budget.remaining, budget.reserved)).is_equal_to(
        (session_cost, 1.0 - session_cost, 0.0),
    )
    assert_that(source.read_text()).is_equal_to("x = 1\ny = 1\n")
    return suggestions, session_cost


@pytest.mark.parametrize("boundary", ["terminal", "github", "markdown", "interactive"])
def test_suggestion_estimates_are_labeled_at_output_boundaries(
    cost_case: tuple[list[AIFixSuggestion], float],
    boundary: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visible estimates describe surviving suggestions even when spend differs."""
    suggestions, session_cost = cost_case
    if boundary == "interactive":
        buf = io.StringIO()
        console = Console(file=buf, width=88, highlight=False)
        monkeypatch.setattr("lintro.ai.interactive.Console", lambda: console)
        monkeypatch.setattr("lintro.ai.interactive.sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("lintro.ai.interactive.click.getchar", lambda: "q")
        accepted, _, applied = review_fixes_interactive(
            suggestions,
            workspace_root=tmp_path,
        )
        assert_that((accepted, applied)).is_equal_to((0, []))
        output = buf.getvalue()
    else:
        monkeypatch.setenv("GITHUB_ACTIONS", str(boundary == "github").lower())
        output = render_fixes(
            suggestions,
            output_format="markdown" if boundary == "markdown" else "auto",
        )
    output = Text.from_ansi(output).plain
    suggestion_cost = sum(s.cost_estimate for s in suggestions)
    print(
        f"boundary={boundary} suggestion_estimate={suggestion_cost} session_spend={session_cost}",
    )
    print(output)
    if not suggestions:
        assert_that(output).is_empty()
    elif suggestion_cost == 0:
        assert_that(output).does_not_contain("Suggestion estimates:", "$", "AI cost:")
    else:
        assert_that(output).contains(
            "Suggestion estimates:",
            format_cost(suggestion_cost),
        )
        assert_that(output).does_not_contain("AI cost:")
        if suggestion_cost != session_cost:
            assert_that(output).does_not_contain(format_cost(session_cost))
        if boundary in {"terminal", "interactive"}:
            tokens = sum(s.input_tokens + s.output_tokens for s in suggestions)
            assert_that(output).contains(f"{tokens} tokens")


@pytest.mark.parametrize("boundary", ["terminal", "github", "markdown"])
def test_hidden_suggestion_estimates_stay_hidden(
    boundary: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The label change preserves the existing show_cost switch."""
    monkeypatch.setenv("GITHUB_ACTIONS", str(boundary == "github").lower())
    output = render_fixes(
        [AIFixSuggestion(file="sample.py", code="B101", cost_estimate=0.125)],
        show_cost=False,
        output_format="markdown" if boundary == "markdown" else "auto",
    )
    assert_that(output).contains("B101")
    assert_that(output).does_not_contain("Suggestion estimates:", "$", "tokens")
