"""Tests for lintro review CLI command."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.exceptions import ReviewExecutionError
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
    assert_that(result.output).contains("--timeout")
    assert_that(result.output).contains("--transport")


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


def test_review_json_output_echoes_payload() -> None:
    """Review command echoes JSON when --output json is used."""
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

    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
        patch(
            "lintro.cli_utils.commands.review.collect_review_context",
            return_value=mock_context,
        ),
        patch(
            "lintro.cli_utils.commands.review.classify_changed_files",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.get_all_checklist_items",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.select_checklist_items",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.format_checklist_for_prompt",
            return_value=("", {}),
        ),
        patch(
            "lintro.cli_utils.commands.review.get_provider",
            return_value=MagicMock(
                model_name="gpt-4o",
                name="openai",
            ),
        ),
        patch(
            "lintro.cli_utils.commands.review.run_review",
            return_value=_empty_result(),
        ),
        patch(
            "lintro.cli_utils.commands.review.render_review_output",
            return_value='{"summary": "ok"}',
        ) as mock_render,
    ):
        result = runner.invoke(
            cli,
            ["review", "--output", "json"],
        )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains('"summary": "ok"')
    assert_that(mock_render.call_args.kwargs).contains_key(
        "checklist_display",
    )


def test_review_passes_transport_override_to_provider() -> None:
    """--transport overrides config when resolving the provider."""
    runner = CliRunner()
    mock_context = MagicMock()
    mock_context.changed_files = []
    mock_context.unified_diff = ""
    mock_config = MagicMock(
        ai=AIConfig(enabled=True, transport=AITransport.API),
    )
    mock_config.review.depth = 1
    mock_config.review.strictness = ReviewStrictness.BALANCED
    mock_config.review.sensitivity = MagicMock()
    mock_config.review.force_semantic_chunking = False
    mock_config.review.checklist_display = ChecklistDisplay.OFF

    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
        patch(
            "lintro.cli_utils.commands.review.collect_review_context",
            return_value=mock_context,
        ),
        patch(
            "lintro.cli_utils.commands.review.classify_changed_files",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.get_all_checklist_items",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.select_checklist_items",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.format_checklist_for_prompt",
            return_value=("", {}),
        ),
        patch(
            "lintro.cli_utils.commands.review.get_provider",
        ) as mock_get_provider,
        patch(
            "lintro.cli_utils.commands.review.run_review",
            return_value=_empty_result(),
        ),
        patch("lintro.cli_utils.commands.review.render_review_output"),
    ):
        mock_get_provider.return_value = MagicMock(
            model_name="gpt-4o",
            name="openai",
        )
        result = runner.invoke(
            cli,
            ["review", "--transport", "cli"],
        )

    assert_that(result.exit_code).is_equal_to(0)
    provider_config = mock_get_provider.call_args.args[0]
    assert_that(provider_config.transport.value).is_equal_to("cli")


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

    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
        patch(
            "lintro.cli_utils.commands.review.collect_review_context",
            return_value=mock_context,
        ),
        patch(
            "lintro.cli_utils.commands.review.classify_changed_files",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.get_all_checklist_items",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.select_checklist_items",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.format_checklist_for_prompt",
            return_value=("", {}),
        ),
        patch(
            "lintro.cli_utils.commands.review.get_provider",
            return_value=MagicMock(
                model_name="gpt-4o",
                name="openai",
            ),
        ),
        patch(
            "lintro.cli_utils.commands.review.run_review",
            return_value=_empty_result(),
        ),
        patch(
            "lintro.cli_utils.commands.review.render_review_output",
        ) as mock_render,
    ):
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_render.call_args.kwargs).contains_key(
        "checklist_display",
    )


def _mock_review_pipeline(
    *,
    mock_collect: MagicMock | None = None,
) -> dict[str, Any]:
    """Return patched review dependencies for CliRunner mode wiring tests."""
    mock_context = MagicMock()
    mock_context.changed_files = []
    mock_context.unified_diff = ""
    mock_config = MagicMock(ai=MagicMock(enabled=True))
    mock_config.review.depth = 1
    mock_config.review.strictness = ReviewStrictness.BALANCED
    mock_config.review.sensitivity = MagicMock()
    mock_config.review.force_semantic_chunking = False
    mock_config.review.checklist_display = ChecklistDisplay.OFF

    collect_patch = (
        patch(
            "lintro.cli_utils.commands.review.collect_review_context",
            mock_collect,
        )
        if mock_collect is not None
        else patch(
            "lintro.cli_utils.commands.review.collect_review_context",
            return_value=mock_context,
        )
    )

    return {
        "require_ai": patch("lintro.cli_utils.commands.review.require_ai"),
        "get_config": patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
        "collect_review_context": collect_patch,
        "classify_changed_files": patch(
            "lintro.cli_utils.commands.review.classify_changed_files",
            return_value=[],
        ),
        "get_all_checklist_items": patch(
            "lintro.cli_utils.commands.review.get_all_checklist_items",
            return_value=[],
        ),
        "select_checklist_items": patch(
            "lintro.cli_utils.commands.review.select_checklist_items",
            return_value=[],
        ),
        "format_checklist_for_prompt": patch(
            "lintro.cli_utils.commands.review.format_checklist_for_prompt",
            return_value=("", {}),
        ),
        "get_provider": patch(
            "lintro.cli_utils.commands.review.get_provider",
            return_value=MagicMock(
                model_name="gpt-4o",
                name="openai",
            ),
        ),
        "run_review": patch(
            "lintro.cli_utils.commands.review.run_review",
            return_value=_empty_result(),
        ),
        "render_review_output": patch(
            "lintro.cli_utils.commands.review.render_review_output",
        ),
    }


def test_review_uncommitted_mode() -> None:
    """Uncommitted mode does not pass an explicit base branch to collection."""
    runner = CliRunner()
    mock_collect = MagicMock(
        return_value=MagicMock(changed_files=[], unified_diff=""),
    )
    patches = _mock_review_pipeline(mock_collect=mock_collect)

    with (
        patches["require_ai"],
        patches["get_config"],
        patches["collect_review_context"],
        patches["classify_changed_files"],
        patches["get_all_checklist_items"],
        patches["select_checklist_items"],
        patches["format_checklist_for_prompt"],
        patches["get_provider"],
        patches["run_review"],
        patches["render_review_output"],
    ):
        result = runner.invoke(cli, ["review", "--uncommitted"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).does_not_contain("Cannot combine")
    assert_that(mock_collect.call_args.kwargs).is_equal_to(
        {
            "base": None,
            "uncommitted": True,
            "pr_number": None,
            "repo": None,
            "paths": None,
        },
    )


def test_review_pr_mode() -> None:
    """PR mode forwards repo without an explicit base branch."""
    runner = CliRunner()
    mock_collect = MagicMock(
        return_value=MagicMock(changed_files=[], unified_diff=""),
    )
    patches = _mock_review_pipeline(mock_collect=mock_collect)

    with (
        patches["require_ai"],
        patches["get_config"],
        patches["collect_review_context"],
        patches["classify_changed_files"],
        patches["get_all_checklist_items"],
        patches["select_checklist_items"],
        patches["format_checklist_for_prompt"],
        patches["get_provider"],
        patches["run_review"],
        patches["render_review_output"],
    ):
        result = runner.invoke(
            cli,
            ["review", "--pr", "5", "--repo", "owner/repo"],
        )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).does_not_contain("explicit base branch")
    assert_that(mock_collect.call_args.kwargs).is_equal_to(
        {
            "base": None,
            "uncommitted": False,
            "pr_number": 5,
            "repo": "owner/repo",
            "paths": None,
        },
    )


def test_review_plain_mode() -> None:
    """Default branch mode succeeds without CI repository env vars."""
    runner = CliRunner()
    mock_collect = MagicMock(
        return_value=MagicMock(changed_files=[], unified_diff=""),
    )
    patches = _mock_review_pipeline(mock_collect=mock_collect)

    with (
        patches["require_ai"],
        patches["get_config"],
        patches["collect_review_context"],
        patches["classify_changed_files"],
        patches["get_all_checklist_items"],
        patches["select_checklist_items"],
        patches["format_checklist_for_prompt"],
        patches["get_provider"],
        patches["run_review"],
        patches["render_review_output"],
    ):
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_collect.call_args.kwargs).is_equal_to(
        {
            "base": None,
            "uncommitted": False,
            "pr_number": None,
            "repo": None,
            "paths": None,
        },
    )


def test_review_plain_with_github_repository_env() -> None:
    """CI GITHUB_REPOSITORY env does not leak into non-PR collection."""
    runner = CliRunner()
    mock_collect = MagicMock(
        return_value=MagicMock(changed_files=[], unified_diff=""),
    )
    patches = _mock_review_pipeline(mock_collect=mock_collect)

    with (
        patches["require_ai"],
        patches["get_config"],
        patches["collect_review_context"],
        patches["classify_changed_files"],
        patches["get_all_checklist_items"],
        patches["select_checklist_items"],
        patches["format_checklist_for_prompt"],
        patches["get_provider"],
        patches["run_review"],
        patches["render_review_output"],
    ):
        result = runner.invoke(
            cli,
            ["review"],
            env={"GITHUB_REPOSITORY": "owner/repo"},
        )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).does_not_contain(
        "Cannot provide repo without pr_number",
    )
    assert_that(mock_collect.call_args.kwargs["repo"]).is_none()


def test_review_repo_without_pr_fails() -> None:
    """Explicit --repo without --pr fails fast instead of reviewing locally."""
    runner = CliRunner()
    mock_config = MagicMock(ai=MagicMock(enabled=True))

    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
    ):
        result = runner.invoke(cli, ["review", "--repo", "owner/repo"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("--repo can only be used with --pr.")


def test_review_post_with_repo_without_pr() -> None:
    """--post with explicit --repo does not require a redundant --pr flag."""
    runner = CliRunner()
    mock_collect = MagicMock(
        return_value=MagicMock(changed_files=[], unified_diff=""),
    )
    patches = _mock_review_pipeline(mock_collect=mock_collect)

    with (
        patches["require_ai"],
        patches["get_config"],
        patches["collect_review_context"],
        patches["classify_changed_files"],
        patches["get_all_checklist_items"],
        patches["select_checklist_items"],
        patches["format_checklist_for_prompt"],
        patches["get_provider"],
        patches["run_review"],
        patches["render_review_output"],
        patch(
            "lintro.cli_utils.commands.review._detect_pr_number_from_env",
            return_value=42,
        ),
        patch("lintro.ai.review.github.post_review_to_github", return_value=True),
    ):
        result = runner.invoke(cli, ["review", "--post", "--repo", "owner/repo"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).does_not_contain(
        "--repo can only be used with --pr.",
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
