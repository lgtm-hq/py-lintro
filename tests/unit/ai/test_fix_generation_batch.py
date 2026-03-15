"""Tests for batch fix generation.

Covers multi-issue batching per file, batch prompt construction,
and batch fallback to single-issue mode.
"""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.ai.fix import (
    generate_fixes,
)
from lintro.ai.providers.base import AIResponse
from tests.unit.ai.conftest import MockAIProvider, MockIssue

# ---------------------------------------------------------------------------
# P3-1: Multi-issue batching per file
# ---------------------------------------------------------------------------


def test_batch_prompt_for_multi_issue_file(tmp_path):
    """Multiple issues in one file should trigger a batch prompt."""
    source = tmp_path / "multi.py"
    source.write_text("x = 1\ny = 2\nz = 3\n")

    issues = [
        MockIssue(
            file=str(source),
            line=1,
            code="B101",
            message="Issue one",
        ),
        MockIssue(
            file=str(source),
            line=3,
            code="E501",
            message="Issue two",
        ),
    ]

    batch_response = AIResponse(
        content=json.dumps(
            [
                {
                    "line": 1,
                    "code": "B101",
                    "original_code": "x = 1",
                    "suggested_code": "x = 2",
                    "explanation": "Fix one",
                    "confidence": "high",
                    "risk_level": "behavioral-risk",
                },
                {
                    "line": 3,
                    "code": "E501",
                    "original_code": "z = 3",
                    "suggested_code": "z = 4",
                    "explanation": "Fix two",
                    "confidence": "medium",
                    "risk_level": "safe-style",
                },
            ],
        ),
        model="mock",
        input_tokens=50,
        output_tokens=50,
        cost_estimate=0.002,
        provider="mock",
    )
    provider = MockAIProvider(responses=[batch_response])

    result = generate_fixes(
        issues,
        provider,
        tool_name="ruff",
        workspace_root=tmp_path,
    )

    # Only 1 provider call (the batch), not 2 single calls
    assert_that(provider.calls).is_length(1)
    prompt = provider.calls[0]["prompt"]
    assert_that(prompt).contains("Issue one")
    assert_that(prompt).contains("Issue two")
    assert_that(prompt).contains("JSON array")

    assert_that(result).is_length(2)
    assert_that(result[0].line).is_equal_to(1)
    assert_that(result[1].line).is_equal_to(3)
    assert_that(result[0].tool_name).is_equal_to("ruff")


def test_batch_fallback_to_single_on_parse_failure(tmp_path):
    """Failed batch parse falls back to single-issue mode."""
    source = tmp_path / "multi.py"
    source.write_text("x = 1\ny = 2\n")

    issues = [
        MockIssue(
            file=str(source),
            line=1,
            code="B101",
            message="Issue one",
        ),
        MockIssue(
            file=str(source),
            line=2,
            code="E501",
            message="Issue two",
        ),
    ]

    # First response (batch) is invalid, subsequent ones are valid single fixes
    responses = [
        AIResponse(
            content="not-a-json-array",
            model="mock",
            input_tokens=10,
            output_tokens=10,
            cost_estimate=0.001,
            provider="mock",
        ),
        AIResponse(
            content=json.dumps(
                {
                    "original_code": "x = 1",
                    "suggested_code": "x = 2",
                    "explanation": "Fix one",
                    "confidence": "high",
                },
            ),
            model="mock",
            input_tokens=10,
            output_tokens=10,
            cost_estimate=0.001,
            provider="mock",
        ),
        AIResponse(
            content=json.dumps(
                {
                    "original_code": "y = 2",
                    "suggested_code": "y = 3",
                    "explanation": "Fix two",
                    "confidence": "high",
                },
            ),
            model="mock",
            input_tokens=10,
            output_tokens=10,
            cost_estimate=0.001,
            provider="mock",
        ),
    ]
    provider = MockAIProvider(responses=responses)

    result = generate_fixes(
        issues,
        provider,
        tool_name="ruff",
        workspace_root=tmp_path,
    )

    # 1 batch call + 2 single fallback calls = 3
    assert_that(provider.calls).is_length(3)
    assert_that(result).is_length(2)
