"""Tests for the idiom-review AI engine (provider mocked)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.providers.response import AIResponse
from lintro.tools.idiom_review.engine import IdiomReviewEngine, IdiomReviewMode
from lintro.tools.idiom_review.signatures import extract_python_signatures


def _config() -> AIConfig:
    return AIConfig(enabled=True, transport=AITransport.API, max_retries=0)


def _response(content: str) -> AIResponse:
    return AIResponse(
        content=content,
        model="test-model",
        input_tokens=1,
        output_tokens=1,
        cost_estimate=0.0,
        provider="anthropic",
    )


def test_review_file_parses_mocked_response() -> None:
    """review_file returns issues parsed from the provider response."""
    provider = MagicMock()
    provider.complete.return_value = _response(
        json.dumps(
            {
                "findings": [
                    {
                        "code": "idiom/python/prefer-any",
                        "line": 2,
                        "message": "Use any().",
                        "confidence": "high",
                    },
                ],
            },
        ),
    )
    engine = IdiomReviewEngine(provider=provider, ai_config=_config())

    issues = engine.review_file(
        file_path="m.py",
        source="found = False\nfor x in items:\n    found = True\n",
    )

    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("idiom/python/prefer-any")
    assert_that(provider.complete.call_count).is_equal_to(1)


def test_review_file_empty_source_skips_provider() -> None:
    """Whitespace-only source never calls the provider."""
    provider = MagicMock()
    engine = IdiomReviewEngine(provider=provider, ai_config=_config())

    issues = engine.review_file(file_path="m.py", source="   \n")

    assert_that(issues).is_empty()
    assert_that(provider.complete.call_count).is_equal_to(0)


def test_review_file_caches_by_content(tmp_path: Path) -> None:
    """A repeat review of identical source is served from cache."""
    provider = MagicMock()
    provider.complete.return_value = _response('{"findings": []}')
    engine = IdiomReviewEngine(
        provider=provider,
        ai_config=_config(),
        workspace_root=tmp_path,
    )
    source = "x = 1\n"

    engine.review_file(file_path="m.py", source=source)
    engine.review_file(file_path="m.py", source=source)

    # Second call hit the cache: the provider was only invoked once.
    assert_that(provider.complete.call_count).is_equal_to(1)


def test_review_duplication_empty_signatures_skips_provider() -> None:
    """No signatures means no duplication call."""
    provider = MagicMock()
    engine = IdiomReviewEngine(provider=provider, ai_config=_config())

    assert_that(engine.review_duplication([])).is_empty()
    assert_that(provider.complete.call_count).is_equal_to(0)


def test_review_duplication_parses_groups() -> None:
    """review_duplication parses duplicate groups into issues."""
    provider = MagicMock()
    provider.complete.return_value = _response(
        json.dumps(
            {
                "duplicate_groups": [
                    {
                        "code": "idiom/cross-file/duplicate-add",
                        "message": "add() duplicated.",
                        "confidence": "medium",
                        "locations": [
                            {"file": "a.py", "line": 1},
                            {"file": "b.py", "line": 1},
                        ],
                    },
                ],
            },
        ),
    )
    engine = IdiomReviewEngine(provider=provider, ai_config=_config())
    sigs = extract_python_signatures("a.py", "def add(a, b):\n    return a + b\n")

    issues = engine.review_duplication(sigs)

    assert_that(issues).is_length(2)
    assert_that(provider.complete.call_count).is_equal_to(1)


@pytest.mark.parametrize(
    "mode",
    [IdiomReviewMode.PER_FILE, IdiomReviewMode.DUPLICATION, IdiomReviewMode.BOTH],
)
def test_mode_enum_values_are_stable(mode: IdiomReviewMode) -> None:
    """Mode enum values are the documented hyphenated strings."""
    assert_that(str(mode)).is_in("per-file", "duplication", "both")
