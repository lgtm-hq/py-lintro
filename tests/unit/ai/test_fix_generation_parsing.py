"""Generation distinguishes malformed responses from valid absence of fixes."""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.ai.fix import generate_fixes
from lintro.ai.providers.base import AIResponse
from tests.unit.ai.conftest import MockAIProvider, MockIssue


def _response(content: str) -> AIResponse:
    """Build a provider response with a controlled payload."""
    return AIResponse(
        content=content,
        model="mock",
        provider="mock",
        input_tokens=10,
        output_tokens=10,
        cost_estimate=0.001,
    )


@pytest.mark.parametrize("malformed", [False, True])
async def test_single_generation_logs_only_malformed_response_as_failure(
    tmp_path,
    malformed,
):
    """A broken answer logs a failure; a valid no-op simply yields no fix."""
    source = tmp_path / "single.py"
    source.write_text("x = 1\n")
    content = (
        "not json"
        if malformed
        else json.dumps(
            {
                "original_code": "x = 1",
                "suggested_code": "x = 1",
            },
        )
    )
    provider = MockAIProvider(responses=[_response(content)])
    messages: list[str] = []
    logger.add(messages.append, format="{message}", level="DEBUG")

    result = await generate_fixes(
        [MockIssue(file=str(source), line=1, code="B101", message="Issue")],
        provider,
        tool_name="ruff",
        workspace_root=tmp_path,
    )

    assert_that(result).is_empty()
    transcript = "".join(messages)
    assert_that("AI fix generation failed" in transcript).is_equal_to(malformed)
    assert_that("malformed response" in transcript).is_equal_to(malformed)
    assert_that(source.read_text()).is_equal_to("x = 1\n")


@pytest.mark.parametrize(
    "content,malformed",
    [
        pytest.param("[]", False, id="empty-array"),
        pytest.param('{"fixes": []}', False, id="empty-wrapper"),
        pytest.param(
            json.dumps(
                [
                    {"original_code": "x = 1", "suggested_code": "x = 1"},
                    {"original_code": "y = 2", "suggested_code": "y = 2"},
                ],
            ),
            False,
            id="all-noop",
        ),
        pytest.param(
            json.dumps(
                [
                    {"original_code": "x = 1", "suggested_code": "x = 9"},
                    {"original_code": "y = 2", "suggested_code": "y = 2"},
                ],
            ),
            False,
            id="partial-noop",
        ),
        pytest.param(
            '[{"original_code": "x = 1", "suggested_code": "x = 9"}]',
            False,
            id="too-few",
        ),
        pytest.param(
            json.dumps(
                [{"original_code": "x = 1", "suggested_code": "x = 9"}] * 3,
            ),
            False,
            id="too-many",
        ),
        pytest.param("not json", True, id="malformed-json"),
    ],
)
async def test_batch_generation_recovers_from_missing_or_malformed_suggestions(
    tmp_path,
    content,
    malformed,
):
    """Count mismatches retry every issue; only malformed replies log failure."""
    source = tmp_path / "batch.py"
    source.write_text("x = 1\ny = 2\n")
    issues = [
        MockIssue(file=str(source), line=line, code="B101", message="Issue")
        for line in (1, 2)
    ]
    fallback = [
        _response(json.dumps({"original_code": original, "suggested_code": suggested}))
        for original, suggested in [("x = 1", "x = 2"), ("y = 2", "y = 3")]
    ]
    provider = MockAIProvider(responses=[_response(content), *fallback])
    messages: list[str] = []
    logger.add(messages.append, format="{message}", level="DEBUG")
    progress: list[tuple[int, int]] = []

    result = await generate_fixes(
        issues,
        provider,
        tool_name="ruff",
        workspace_root=tmp_path,
        max_workers=1,
        progress_callback=lambda done, total: progress.append((done, total)),
    )

    assert_that([s.suggested_code for s in result]).is_equal_to(
        ["x = 2", "y = 3"],
    )
    assert_that([s.line for s in result]).is_equal_to([1, 2])
    assert_that(progress).is_equal_to([(1, 2), (2, 2)])
    transcript = "".join(messages)
    assert_that("Batch AI fix generation failed" in transcript).is_equal_to(malformed)
    assert_that("malformed response" in transcript).is_equal_to(malformed)
    assert_that("count mismatch" in transcript).is_equal_to(not malformed)
