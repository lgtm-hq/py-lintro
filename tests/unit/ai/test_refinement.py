"""Tests for AI fix refinement module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.models import AIFixSuggestion
from lintro.ai.refinement import _revert_fix, refine_unverified_fixes
from lintro.ai.validation import ValidationResult


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


def test_refine_returns_empty_when_no_unverified_keys(tmp_path: Path) -> None:
    """refine_unverified_fixes returns empty list when no detail matches."""
    suggestion = _make_suggestion()
    validation = ValidationResult(
        verified=1,
        unverified=0,
        details=["[E001] test.py:10 — fix verified"],
    )
    provider = MagicMock()
    ai_config = MagicMock()
    ai_config.fallback_models = []
    ai_config.max_retries = 0
    ai_config.retry_base_delay = 1.0
    ai_config.retry_max_delay = 30.0
    ai_config.retry_backoff_factor = 2.0

    refined, cost = refine_unverified_fixes(
        applied_suggestions=[suggestion],
        validation=validation,
        provider=provider,
        ai_config=ai_config,
        workspace_root=tmp_path,
    )

    assert_that(refined).is_empty()
    assert_that(cost).is_equal_to(0.0)


def test_refine_parses_detail_strings_correctly(tmp_path: Path) -> None:
    """Parses '[code] file:line - issue still present' details."""
    suggestion = _make_suggestion(code="W123", line=42, file="src/main.py")
    validation = ValidationResult(
        verified=0,
        unverified=1,
        details=["[W123] src/main.py:42 — issue still present"],
    )

    provider = MagicMock()
    ai_config = MagicMock()
    ai_config.fallback_models = []
    ai_config.max_retries = 0
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

        refined, cost = refine_unverified_fixes(
            applied_suggestions=[suggestion],
            validation=validation,
            provider=provider,
            ai_config=ai_config,
            workspace_root=tmp_path,
        )

    assert_that(refined).is_length(1)
    assert_that(cost).is_close_to(0.001, 0.0001)


def test_refine_skips_when_revert_fails(tmp_path: Path) -> None:
    """refine_unverified_fixes skips a suggestion when revert fails."""
    suggestion = _make_suggestion(code="E001", line=10)
    validation = ValidationResult(
        verified=0,
        unverified=1,
        details=["[E001] test.py:10 — issue still present"],
    )

    provider = MagicMock()
    ai_config = MagicMock()
    ai_config.fallback_models = []
    ai_config.max_retries = 0
    ai_config.retry_base_delay = 1.0
    ai_config.retry_max_delay = 30.0
    ai_config.retry_backoff_factor = 2.0

    with patch("lintro.ai.refinement._revert_fix") as mock_revert:
        mock_revert.return_value = False
        refined, cost = refine_unverified_fixes(
            applied_suggestions=[suggestion],
            validation=validation,
            provider=provider,
            ai_config=ai_config,
            workspace_root=tmp_path,
        )

    assert_that(refined).is_empty()
    assert_that(cost).is_equal_to(0.0)


def test_refine_skips_when_parse_returns_none(tmp_path: Path) -> None:
    """refine_unverified_fixes skips when _parse_fix_response returns None."""
    suggestion = _make_suggestion(code="E001", line=10)
    validation = ValidationResult(
        verified=0,
        unverified=1,
        details=["[E001] test.py:10 — issue still present"],
    )

    provider = MagicMock()
    ai_config = MagicMock()
    ai_config.fallback_models = []
    ai_config.max_retries = 0
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

        refined, cost = refine_unverified_fixes(
            applied_suggestions=[suggestion],
            validation=validation,
            provider=provider,
            ai_config=ai_config,
            workspace_root=tmp_path,
        )

    assert_that(refined).is_empty()
