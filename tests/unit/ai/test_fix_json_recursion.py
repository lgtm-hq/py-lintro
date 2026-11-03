"""Real JSON recursion failures stay inside the AI fix parsing boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.ai import fix
from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.fix_parsing import (
    AIFixParseError,
    parse_batch_response,
    parse_fix_response,
)
from lintro.ai.models import AIFixSuggestion
from lintro.ai.providers.base import AIResponse
from lintro.ai.refinement import refine_unverified_fixes
from lintro.ai.validation import ValidationResult
from tests.unit.ai.conftest import MockAIProvider, MockIssue


def _json_token(response_kind: str) -> str:
    """Build balanced nested JSON without changing the interpreter's limit."""
    if response_kind == "malformed":
        return "?"
    # Python 3.14's C decoder follows the native stack limit, so merely
    # exceeding sys.getrecursionlimit() does not reproduce this failure.
    depth = 100_000 if response_kind == "deep" else 8
    return "[" * depth + "0" + "]" * depth


def _content(shape: str, token: str) -> str:
    """Put nesting in ignored metadata beside otherwise valid fix fields."""
    single = shape == "single"
    entry = json.dumps(
        {
            "line": 17 if single else 2,
            "code": "B101",
            "original_code": "x = 1" if single else "y = 2",
            "suggested_code": "x = 9" if single else "y = 9",
            "explanation": "private-recursion-payload",
            "metadata": "TOKEN",
        },
    ).replace('"TOKEN"', token)
    if single:
        return entry
    neighbor = json.dumps(
        {
            "line": 1,
            "code": "B101",
            "original_code": "x = 1",
            "suggested_code": "x = 9",
        },
    )
    batch = "[" + neighbor + "," + entry + "]"
    return '{"fixes": ' + batch + "}" if shape == "wrapped" else batch


def _response(content: str) -> AIResponse:
    """Return controlled content with real usage for the invocation path."""
    return AIResponse(
        content=content,
        model="mock",
        provider="mock",
        input_tokens=24,
        output_tokens=12,
        cost_estimate=0.006,
    )


@pytest.fixture
def fix_budgets(monkeypatch: pytest.MonkeyPatch) -> list[CostBudget]:
    """Observe actual provider charges even when response parsing fails."""
    budgets: list[CostBudget] = []

    def new_budget() -> CostBudget:
        budget = CostBudget()
        budgets.append(budget)
        return budget

    monkeypatch.setattr(fix, "CostBudget", new_budget)
    return budgets


@pytest.fixture
def diagnostics() -> list[tuple[str, bool]]:
    """Capture messages and both ways loguru carries exception metadata."""
    records: list[tuple[str, bool]] = []
    logger.add(
        lambda message: records.append(
            (
                message.record["message"],
                "exc_info" in message.record["extra"]
                or message.record["exception"] is not None,
            ),
        ),
        level="DEBUG",
    )
    return records


def _quiet_transcript(diagnostics: list[tuple[str, bool]], content: str) -> str:
    """Assert diagnostics contain neither response content nor traceback noise."""
    transcript = "\n".join(text for text, _ in diagnostics)
    assert_that(content in transcript).is_false()
    assert_that(transcript).does_not_contain(
        "private-recursion-payload",
        "RecursionError",
        "JSONDecodeError",
        "Traceback",
    )
    assert_that(any(noisy for _, noisy in diagnostics)).is_false()
    return transcript


@pytest.mark.parametrize("shape", ["single", "bare", "wrapped"])
@pytest.mark.parametrize("response_kind", ["deep", "ordinary", "malformed"])
def test_fix_decoder_normalizes_recursion_with_ordinary_controls(
    shape: str,
    response_kind: str,
) -> None:
    """Single and both batch containers use the actual decoder and retain causes."""
    content = _content(shape, _json_token(response_kind))
    limit = sys.getrecursionlimit()

    def parse() -> list[AIFixSuggestion | None]:
        if shape == "single":
            return [parse_fix_response(content, "nested.py", 2, "B101")]
        return list(parse_batch_response(content, "nested.py"))

    if response_kind == "ordinary":
        suggestions = parse()
        assert_that([s.line for s in suggestions if s is not None]).is_equal_to(
            [2] if shape == "single" else [1, 2],
        )
        assert_that([s.suggested_code for s in suggestions if s]).is_equal_to(
            ["x = 9"] if shape == "single" else ["x = 9", "y = 9"],
        )
    else:
        cause = RecursionError if response_kind == "deep" else json.JSONDecodeError
        with pytest.raises(cause):
            json.loads(content)
        with pytest.raises(AIFixParseError) as error:
            parse()
        assert_that(error.value.__cause__).is_type_of(cause)
        assert_that(str(error.value)).is_equal_to(
            (
                "Invalid JSON in AI fix response for nested.py:2"
                if shape == "single"
                else "Invalid JSON in batch AI response for nested.py"
            ),
        ).does_not_contain(content, "private-recursion-payload")
    assert_that(sys.getrecursionlimit()).is_equal_to(limit)


@pytest.mark.parametrize("shape", ["single", "bare", "wrapped"])
@pytest.mark.parametrize("response_kind", ["deep", "ordinary", "malformed"])
async def test_generation_recursion_diagnostic_and_whole_batch_fallback(
    tmp_path: Path,
    ai_config: AIConfig,
    fix_budgets: list[CostBudget],
    diagnostics: list[tuple[str, bool]],
    shape: str,
    response_kind: str,
) -> None:
    """Reject deep replies concisely, retry every batch issue, and never edit source."""
    source = tmp_path / "nested.py"
    original = b"x = 1\ny = 2\n"
    source.write_bytes(original)
    content = _content(shape, _json_token(response_kind))
    fallback = [
        _response(json.dumps({"original_code": old, "suggested_code": new}))
        for old, new in [("x = 1", "x = 2"), ("y = 2", "y = 3")]
    ]
    provider = MockAIProvider(responses=[_response(content), *fallback])
    single = shape == "single"
    ordinary = response_kind == "ordinary"
    progress: list[tuple[int, int]] = []

    suggestions = await fix.generate_fixes(
        [
            MockIssue(file=str(source), line=line, code="B101", message="Issue")
            for line in ([1] if single else [1, 2])
        ],
        provider,
        ai_config=ai_config,
        tool_name="ruff",
        workspace_root=tmp_path,
        max_workers=1,
        progress_callback=lambda done, total: progress.append((done, total)),
    )

    transcript = _quiet_transcript(diagnostics, content)
    assert_that("malformed response (Invalid JSON" in transcript).is_equal_to(
        not ordinary,
    )
    assert_that("falling back to single-issue mode" in transcript).is_equal_to(
        not single and not ordinary,
    )
    expected = (
        (["x = 9"] if ordinary else [])
        if single
        else (["x = 9", "y = 9"] if ordinary else ["x = 2", "y = 3"])
    )
    assert_that([s.suggested_code for s in suggestions]).is_equal_to(expected)
    assert_that([s.line for s in suggestions]).is_equal_to(
        ([1] if ordinary else []) if single else [1, 2],
    )
    usage = (12, 6, 0.003) if not single and ordinary else (24, 12, 0.006)
    assert_that(
        [(s.input_tokens, s.output_tokens, s.cost_estimate) for s in suggestions],
    ).is_equal_to([usage] * len(expected))
    assert_that([budget.spent for budget in fix_budgets]).is_equal_to(
        [0.006] * (1 if single or ordinary else 3),
    )
    assert_that([budget.reserved for budget in fix_budgets]).contains_only(0.0)
    assert_that(progress).is_equal_to(
        [(1, 1)] if single else ([(2, 2)] if ordinary else [(1, 2), (2, 2)]),
    )
    assert_that(source.read_bytes()).is_equal_to(original)


@pytest.mark.parametrize("response_kind", ["deep", "ordinary", "malformed"])
async def test_refinement_recursion_keeps_reverted_bytes_and_provider_charge(
    tmp_path: Path,
    ai_config: AIConfig,
    fix_budgets: list[CostBudget],
    diagnostics: list[tuple[str, bool]],
    response_kind: str,
) -> None:
    """The shared single parser protects refinement after its real revert."""
    source = tmp_path / "nested.py"
    source.write_bytes(b"# context\nx = 2\n")
    content = _content("single", _json_token(response_kind))
    suggestion = AIFixSuggestion(
        file="nested.py",
        line=2,
        code="B101",
        tool_name="ruff",
        original_code="x = 1",
        suggested_code="x = 2",
    )

    refined, cost = await refine_unverified_fixes(
        applied_suggestions=[suggestion],
        validation=ValidationResult(
            unverified=1,
            details=["[B101] nested.py:2 — issue still present"],
        ),
        provider=MockAIProvider(responses=[_response(content)]),
        ai_config=ai_config,
        workspace_root=tmp_path,
    )

    transcript = _quiet_transcript(diagnostics, content)
    assert_that([budget.spent for budget in fix_budgets]).is_equal_to([0.006])
    assert_that(fix_budgets[0].reserved).is_equal_to(0.0)
    if response_kind == "ordinary":
        assert_that(refined).is_length(1)
        assert_that(refined[0].line).is_equal_to(2)
        assert_that(refined[0].diff).contains("-x = 1", "+x = 9")
        assert_that(
            (refined[0].input_tokens, refined[0].output_tokens, cost),
        ).is_equal_to((24, 12, 0.006))
        assert_that(source.read_bytes()).is_equal_to(b"# context\nx = 9\n")
        assert_that(transcript).does_not_contain("Refinement failed")
    else:
        assert_that(refined).is_empty()
        assert_that(source.read_bytes()).is_equal_to(b"# context\nx = 1\n")
        assert_that(transcript).contains(
            "Refinement failed for nested.py:2: malformed response "
            "(Invalid JSON in AI fix response for nested.py:2)",
        )
        # Returned failed-parse cost undercount remains a separate #3116 follow-up;
        # assert the actual provider charge without pinning that undercount here.
