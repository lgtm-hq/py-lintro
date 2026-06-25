"""Tests for lintro review CLI command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.review.exceptions import ReviewExecutionError
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.cli import cli


def _empty_result() -> ReviewResult:
    return ReviewResult(
        metadata=ReviewMetadata(
            model="gpt-4o",
            provider="openai",
            context_window=128_000,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=0,
            files_total=0,
            checklist_items=0,
        ),
        summary="No changes found to review.",
        checklist=(),
        findings=(),
    )


def test_review_help_shows_flags() -> None:
    """Review command help lists primary flags."""
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("--base")
    assert_that(result.output).contains("--with-lint")
    assert_that(result.output).contains("--depth")
    assert_that(result.output).contains("--show-checklist")


def test_review_alias_rev_works() -> None:
    """Alias rev resolves to the review command help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["rev", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("AI-powered diff-based code review")


def test_review_requires_ai_packages() -> None:
    """Missing AI packages produce a usage error."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.review.require_ai") as mock_require:
        mock_require.side_effect = Exception("AI packages not installed")
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_not_equal_to(0)


def test_review_exits_zero_without_p1_findings() -> None:
    """Review command exits 0 when no P1 findings exist."""
    runner = CliRunner()
    mock_context = MagicMock()
    mock_context.changed_files = []
    mock_context.unified_diff = ""
    mock_config = MagicMock(ai=MagicMock(enabled=True))
    mock_config.review.depth = 1
    mock_config.review.strictness = ReviewStrictness.BALANCED
    mock_config.review.sensitivity = MagicMock()
    mock_config.review.force_semantic_chunking = False
    mock_config.review.checklist_display = ChecklistDisplay.OFF

    with patch("lintro.cli_utils.commands.review.require_ai"):
        with patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ):
            with patch(
                "lintro.cli_utils.commands.review.collect_review_context",
                return_value=mock_context,
            ):
                with patch(
                    "lintro.cli_utils.commands.review.classify_changed_files",
                    return_value=[],
                ):
                    with patch(
                        "lintro.cli_utils.commands.review.get_all_checklist_items",
                        return_value=[],
                    ):
                        with patch(
                            "lintro.cli_utils.commands.review.select_checklist_items",
                            return_value=[],
                        ):
                            with patch(
                                "lintro.cli_utils.commands.review.format_checklist_for_prompt",
                                return_value=("", {}),
                            ):
                                with patch(
                                    "lintro.cli_utils.commands.review.get_provider",
                                    return_value=MagicMock(
                                        model_name="gpt-4o",
                                        name="openai",
                                    ),
                                ):
                                    with patch(
                                        "lintro.cli_utils.commands.review.run_review",
                                        return_value=_empty_result(),
                                    ):
                                        with patch(
                                            "lintro.cli_utils.commands.review.render_review_output",
                                        ) as mock_render:
                                            result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_render.call_args.kwargs).contains_key(
        "checklist_display",
    )


def test_review_failure_renders_friendly_error_without_traceback() -> None:
    """Mid-review failures show a Rich panel instead of a traceback."""
    runner = CliRunner()
    mock_context = MagicMock()
    mock_context.changed_files = [MagicMock(path="src/a.py")]
    mock_context.unified_diff = "diff"

    execution_error = ReviewExecutionError(
        message="Review aborted before all chunks completed.",
        chunk_index=2,
        total_chunks=6,
        step="reviewing",
        completed_chunks=2,
        cause_message="Cursor CLI timed out after 300s",
    )
    mock_config = MagicMock(ai=MagicMock(enabled=True))
    mock_config.review.depth = 1
    mock_config.review.strictness = ReviewStrictness.BALANCED
    mock_config.review.sensitivity = MagicMock()
    mock_config.review.force_semantic_chunking = False
    mock_config.review.checklist_display = ChecklistDisplay.OFF

    with patch("lintro.cli_utils.commands.review.require_ai"):
        with patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ):
            with patch(
                "lintro.cli_utils.commands.review.collect_review_context",
                return_value=mock_context,
            ):
                with patch(
                    "lintro.cli_utils.commands.review.classify_changed_files",
                    return_value=[],
                ):
                    with patch(
                        "lintro.cli_utils.commands.review.get_all_checklist_items",
                        return_value=[],
                    ):
                        with patch(
                            "lintro.cli_utils.commands.review.select_checklist_items",
                            return_value=[],
                        ):
                            with patch(
                                "lintro.cli_utils.commands.review.format_checklist_for_prompt",
                                return_value=("", {}),
                            ):
                                with patch(
                                    "lintro.cli_utils.commands.review.get_provider",
                                    return_value=MagicMock(
                                        model_name="auto",
                                        name="cursor",
                                    ),
                                ):
                                    with patch(
                                        "lintro.cli_utils.commands.review.run_review",
                                        side_effect=execution_error,
                                    ):
                                        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("Review failed")
    assert_that(result.output).contains("chunk 3/6")
    assert_that(result.output).contains("api_timeout")
    assert_that(result.output).does_not_contain("Traceback")
