"""Tests for AI fix refinement module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.ai.models import AIFixSuggestion
from lintro.ai.providers.base import AIResponse
from lintro.ai.refinement import _revert_fix, refine_unverified_fixes
from lintro.ai.validation import ValidationResult
from tests.unit.ai.conftest import MockAIProvider


def _make_suggestion(**kwargs: object) -> AIFixSuggestion:
    """Create a minimal AIFixSuggestion for tests."""
    defaults = {
        "file": "test.py",
        "line": 10,
        "code": "E001",
        "original_code": "x = 1",
        "suggested_code": "x = 2",
        "tool_name": "ruff",
    }
    defaults.update(kwargs)
    return AIFixSuggestion(**defaults)  # type: ignore[arg-type]


# -- _revert_fix -----------------------------------------------------------


def test_revert_fix_calls_apply_fixes_with_reversed_suggestion(
    tmp_path: Path,
) -> None:
    """_revert_fix creates a reverse suggestion and calls apply_fixes."""
    suggestion = _make_suggestion(
        original_code="old_code",
        suggested_code="new_code",
    )

    with patch("lintro.ai.refinement.apply_fixes") as mock_apply:
        mock_apply.return_value = [suggestion]
        result = _revert_fix(suggestion, tmp_path)

    assert_that(result).is_true()
    mock_apply.assert_called_once()
    # Check that the reverse suggestion swaps original and suggested code
    call_args = mock_apply.call_args
    reverse_suggestions = call_args[0][0]
    assert_that(reverse_suggestions).is_length(1)
    assert_that(reverse_suggestions[0].original_code).is_equal_to("new_code")
    assert_that(reverse_suggestions[0].suggested_code).is_equal_to("old_code")


def test_revert_fix_returns_false_when_apply_fails(tmp_path: Path) -> None:
    """_revert_fix returns False when apply_fixes returns empty list."""
    suggestion = _make_suggestion()

    with patch("lintro.ai.refinement.apply_fixes") as mock_apply:
        mock_apply.return_value = []
        result = _revert_fix(suggestion, tmp_path)

    assert_that(result).is_false()


# -- refine_unverified_fixes -----------------------------------------------


async def test_refine_returns_empty_when_no_unverified_keys(tmp_path: Path) -> None:
    """refine_unverified_fixes returns empty list when no detail matches."""
    suggestion = _make_suggestion()
    validation = ValidationResult(
        verified=1,
        unverified=0,
        details=["[E001] test.py:10 — fix verified"],
    )
    provider = MagicMock()
    provider.complete = AsyncMock()
    ai_config = MagicMock()
    ai_config.fallback_models = []
    ai_config.max_retries = 0
    ai_config.rate_limit_max_retries = 0
    ai_config.retry_base_delay = 1.0
    ai_config.retry_max_delay = 30.0
    ai_config.retry_backoff_factor = 2.0

    refined, cost = await refine_unverified_fixes(
        applied_suggestions=[suggestion],
        validation=validation,
        provider=provider,
        ai_config=ai_config,
        workspace_root=tmp_path,
    )

    assert_that(refined).is_empty()
    assert_that(cost).is_equal_to(0.0)


@pytest.mark.parametrize(
    "content,malformed",
    [
        pytest.param("not json", True, id="invalid-json"),
        pytest.param("[]", True, id="wrong-shape"),
        pytest.param("{}", True, id="missing-snippets"),
        pytest.param(
            '{"original_code": "x = 1", "suggested_code": []}',
            True,
            id="invalid-snippet",
        ),
        *[
            pytest.param(
                json.dumps({"original_code": original, "suggested_code": suggested}),
                False,
                id=name,
            )
            for name, original, suggested in [
                ("noop", "x = 1", "x = 1"),
                ("empty-original", "", "x = 3"),
                ("empty-suggested", "x = 1", ""),
            ]
        ],
    ],
)
async def test_refinement_parse_diagnostic_preserves_reverted_file(
    tmp_path,
    ai_config,
    content,
    malformed,
):
    """Bad replies log concise failures; valid absence leaves the reverted file."""
    source = tmp_path / "test.py"
    source.write_text("x = 2\n")
    suggestion = _make_suggestion(line=1)
    provider = MockAIProvider(
        responses=[
            AIResponse(
                content=content,
                model="mock",
                provider="mock",
                input_tokens=10,
                output_tokens=10,
                cost_estimate=0.001,
            ),
        ],
    )
    diagnostics: list[tuple[str, bool]] = []
    logger.add(
        lambda message: diagnostics.append(
            (
                message.record["message"],
                "exc_info" in message.record["extra"]
                or message.record["exception"] is not None,
            ),
        ),
        level="DEBUG",
    )

    refined, cost = await refine_unverified_fixes(
        applied_suggestions=[suggestion],
        validation=ValidationResult(
            unverified=1,
            details=["[E001] test.py:1 — issue still present"],
        ),
        provider=provider,
        ai_config=ai_config,
        workspace_root=tmp_path,
    )

    assert_that(refined).is_empty()
    assert_that(cost).is_equal_to(0.0)
    assert_that(source.read_text()).is_equal_to("x = 1\n")
    failures = [
        (text, noisy) for text, noisy in diagnostics if "Refinement failed" in text
    ]
    assert_that(failures).is_length(int(malformed))
    if malformed:
        text, noisy = failures[0]
        assert_that(text).starts_with(
            "Refinement failed for test.py:1: malformed response (",
        )
        assert_that(noisy).is_false()


@pytest.mark.parametrize("status", ["fix verified", "tool check unmeasured"])
async def test_refinement_leaves_verified_or_unmeasured_file_alone(
    tmp_path,
    ai_config,
    status,
):
    """Only a measured still-present issue can trigger a revert and refinement."""
    source = tmp_path / "test.py"
    source.write_text("x = 2\n")

    refined, cost = await refine_unverified_fixes(
        applied_suggestions=[_make_suggestion(line=1)],
        validation=ValidationResult(
            verified=int(status == "fix verified"),
            unverified=int(status == "tool check unmeasured"),
            details=[f"[E001] test.py:1 — {status}"],
        ),
        provider=MockAIProvider(),
        ai_config=ai_config,
        workspace_root=tmp_path,
    )

    assert_that((refined, cost)).is_equal_to(([], 0.0))
    assert_that(source.read_text()).is_equal_to("x = 2\n")


async def test_refine_parses_detail_strings_correctly(tmp_path: Path) -> None:
    """Parses '[code] file:line - issue still present' details."""
    suggestion = _make_suggestion(code="W123", line=42, file="src/main.py")
    validation = ValidationResult(
        verified=0,
        unverified=1,
        details=["[W123] src/main.py:42 — issue still present"],
    )

    provider = MagicMock()
    provider.complete = AsyncMock()
    ai_config = MagicMock()
    ai_config.fallback_models = []
    ai_config.max_retries = 0
    ai_config.rate_limit_max_retries = 0
    ai_config.retry_base_delay = 1.0
    ai_config.retry_max_delay = 30.0
    ai_config.retry_backoff_factor = 2.0
    ai_config.context_lines = 15
    ai_config.max_tokens = 4096
    ai_config.api_timeout = 60.0
    ai_config.fix_search_radius = 5

    with (
        patch("lintro.ai.refinement._revert_fix") as mock_revert,
        patch("lintro.ai.refinement.read_file_safely") as mock_read,
        patch("lintro.ai.refinement.extract_context") as mock_ctx,
        patch("lintro.ai.refinement.parse_fix_response") as mock_parse,
        patch("lintro.ai.refinement.apply_fixes") as mock_apply,
    ):
        mock_revert.return_value = True
        mock_read.return_value = "file content\n"
        mock_ctx.return_value = ("context", 1, 10)

        mock_response = MagicMock()
        mock_response.content = "response content"
        mock_response.input_tokens = 100
        mock_response.output_tokens = 50
        mock_response.cost_estimate = 0.001
        provider.complete.return_value = mock_response

        refined_sugg = _make_suggestion(code="W123", line=42)
        refined_sugg.input_tokens = 100
        refined_sugg.output_tokens = 50
        refined_sugg.cost_estimate = 0.001
        mock_parse.return_value = refined_sugg
        mock_apply.return_value = [refined_sugg]

        refined, cost = await refine_unverified_fixes(
            applied_suggestions=[suggestion],
            validation=validation,
            provider=provider,
            ai_config=ai_config,
            workspace_root=tmp_path,
        )

    assert_that(refined).is_length(1)
    assert_that(cost).is_close_to(0.001, 0.0001)


async def test_refine_skips_when_revert_fails(tmp_path: Path) -> None:
    """refine_unverified_fixes skips a suggestion when revert fails."""
    suggestion = _make_suggestion(code="E001", line=10)
    validation = ValidationResult(
        verified=0,
        unverified=1,
        details=["[E001] test.py:10 — issue still present"],
    )

    provider = MagicMock()
    provider.complete = AsyncMock()
    ai_config = MagicMock()
    ai_config.fallback_models = []
    ai_config.max_retries = 0
    ai_config.rate_limit_max_retries = 0
    ai_config.retry_base_delay = 1.0
    ai_config.retry_max_delay = 30.0
    ai_config.retry_backoff_factor = 2.0

    with patch("lintro.ai.refinement._revert_fix") as mock_revert:
        mock_revert.return_value = False
        refined, cost = await refine_unverified_fixes(
            applied_suggestions=[suggestion],
            validation=validation,
            provider=provider,
            ai_config=ai_config,
            workspace_root=tmp_path,
        )

    assert_that(refined).is_empty()
    assert_that(cost).is_equal_to(0.0)


async def test_refine_skips_when_parse_returns_none(tmp_path: Path) -> None:
    """refine_unverified_fixes skips when _parse_fix_response returns None."""
    suggestion = _make_suggestion(code="E001", line=10)
    validation = ValidationResult(
        verified=0,
        unverified=1,
        details=["[E001] test.py:10 — issue still present"],
    )

    provider = MagicMock()
    provider.complete = AsyncMock()
    ai_config = MagicMock()
    ai_config.fallback_models = []
    ai_config.max_retries = 0
    ai_config.rate_limit_max_retries = 0
    ai_config.retry_base_delay = 1.0
    ai_config.retry_max_delay = 30.0
    ai_config.retry_backoff_factor = 2.0
    ai_config.context_lines = 15
    ai_config.max_tokens = 4096
    ai_config.api_timeout = 60.0
    ai_config.fix_search_radius = 5

    with (
        patch("lintro.ai.refinement._revert_fix") as mock_revert,
        patch("lintro.ai.refinement.read_file_safely") as mock_read,
        patch("lintro.ai.refinement.extract_context") as mock_ctx,
        patch("lintro.ai.refinement.parse_fix_response") as mock_parse,
    ):
        mock_revert.return_value = True
        mock_read.return_value = "file content\n"
        mock_ctx.return_value = ("context", 1, 10)

        mock_response = MagicMock()
        mock_response.content = "response content"
        mock_response.input_tokens = 100
        mock_response.output_tokens = 50
        mock_response.cost_estimate = 0.001
        provider.complete.return_value = mock_response

        mock_parse.return_value = None

        refined, _cost = await refine_unverified_fixes(
            applied_suggestions=[suggestion],
            validation=validation,
            provider=provider,
            ai_config=ai_config,
            workspace_root=tmp_path,
        )

    assert_that(refined).is_empty()
