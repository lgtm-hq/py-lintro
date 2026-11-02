"""Generation distinguishes malformed responses from valid absence of fixes."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.ai.cli_schemas import FIX_BATCH_KEY
from lintro.ai.fix import generate_fixes
from lintro.ai.providers.base import AIResponse
from tests.unit.ai.conftest import MockAIProvider, MockIssue

_MALFORMED_BATCH_FIELDS: list[tuple[str, Mapping[str, object]]] = [
    ("infinite-line", {"line": float("inf")}),
    ("nan-line", {"line": float("nan")}),
    ("mapping-rule-code", {"code": {"rule": "B101"}}),
    ("list-rule-code", {"code": ["B101"]}),
    ("mapping-original", {"original_code": {"text": "y = 2"}}),
    ("list-original", {"original_code": ["y = 2"]}),
    ("mapping-suggested", {"suggested_code": {"text": "y = 9"}}),
    ("list-suggested", {"suggested_code": ["y = 9"]}),
    *[
        (
            f"{name}-{pairing}",
            {"original_code": original, "suggested_code": suggested},
        )
        for name, value in [
            ("empty-mapping", {}),
            ("mapping", {"text": "y = 2"}),
            ("empty-list", []),
            ("list", ["y = 2"]),
        ]
        for pairing, original, suggested in [
            ("empty-original", "", value),
            ("empty-suggested", value, ""),
            ("identical", value, value),
        ]
    ],
]


def _batch_content_with_candidate(
    fields: Mapping[str, object],
    *,
    wrapped: bool,
    invalid_first: bool,
) -> str:
    """Pair a candidate fix with a valid fix that fallback must discard.

    Args:
        fields: Fields that invalidate or empty the second source line's fix.
        wrapped: Whether to use the CLI schema object instead of a bare array.
        invalid_first: Whether the candidate fix precedes the valid fix.

    Returns:
        The batch response to feed to generation.
    """
    entries = [
        {"original_code": "x = 1", "suggested_code": "x = 9"},
        {"original_code": "y = 2", "suggested_code": "y = 9", **fields},
    ]
    if invalid_first:
        entries.reverse()
    return json.dumps({FIX_BATCH_KEY: entries} if wrapped else entries)


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
        *[
            pytest.param(
                _batch_content_with_candidate(
                    fields,
                    wrapped=wrapped,
                    invalid_first=invalid_first,
                ),
                False,
                id=f"{name}-wrapped={wrapped}-invalid-first={invalid_first}",
            )
            for name, fields in [
                ("empty-original", {"original_code": ""}),
                ("empty-suggested", {"suggested_code": ""}),
                ("both-empty", {"original_code": "", "suggested_code": ""}),
                ("identical-strings", {"suggested_code": "y = 2"}),
            ]
            for wrapped in (False, True)
            for invalid_first in (False, True)
        ],
        *[
            pytest.param(
                _batch_content_with_candidate(
                    fields,
                    wrapped=wrapped,
                    invalid_first=invalid_first,
                ),
                True,
                id=f"{name}-wrapped={wrapped}-invalid-first={invalid_first}",
            )
            for name, fields in _MALFORMED_BATCH_FIELDS
            for wrapped in (False, True)
            for invalid_first in (False, True)
        ],
    ],
)
async def test_batch_generation_recovers_from_missing_or_malformed_suggestions(
    tmp_path,
    content,
    malformed,
):
    """Retry every issue, discarding valid batch fixes on any malformed entry."""
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
    assert_that(source.read_text()).is_equal_to("x = 1\ny = 2\n")
    transcript = "".join(messages)
    assert_that("Batch AI fix generation failed" in transcript).is_equal_to(malformed)
    assert_that("malformed response" in transcript).is_equal_to(malformed)
    assert_that("count mismatch" in transcript).is_equal_to(not malformed)
