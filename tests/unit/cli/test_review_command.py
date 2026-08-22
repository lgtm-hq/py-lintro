"""Tests for lintro review CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.provider_enum import AIProvider
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.custom_agent_mode import CustomAgentMode
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.exceptions import ReviewExecutionError
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.cli import cli
from lintro.cli_utils.commands.review import (
    _cli_overrides,
    _describe_config_source,
    _merge_advisory_into_json,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.idiom_review.idiom_review_issue import IdiomReviewIssue


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
    assert_that(result.output).contains("--provider")
    assert_that(result.output).contains("--model")
    assert_that(result.output).contains("--max-cost-usd")


def test_review_invalid_provider_env_exits_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo'd ``LINTRO_AI_PROVIDER`` is a usage error, not a traceback."""
    monkeypatch.setenv("LINTRO_AI_PROVIDER", "cursur")
    mock_config = MagicMock(ai={"enabled": True, "review": True})
    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
    ):
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(2)
    assert_that(result.output).contains("LINTRO_AI_PROVIDER='cursur'")
    assert_that(result.output).does_not_contain("Traceback")


def test_review_nonnumeric_max_cost_usd_exits_two() -> None:
    """Non-numeric ``--max-cost-usd`` uses the overlay error, not Click's float."""
    mock_config = MagicMock(ai={"enabled": True, "review": True})
    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
    ):
        result = runner.invoke(cli, ["review", "--max-cost-usd", "plenty"])

    assert_that(result.exit_code).is_equal_to(2)
    assert_that(result.output).contains("--max-cost-usd='plenty'")
    assert_that(result.output).contains("0 for uncapped")
    assert_that(result.output).does_not_contain("Traceback")


def test_review_max_cost_flag_beats_transport_profile() -> None:
    """``--max-cost-usd 0`` lifts a YAML transport-profile cap (#2024)."""
    runner = CliRunner()
    mock_context = MagicMock()
    mock_context.changed_files = []
    mock_context.unified_diff = ""
    mock_config = MagicMock(
        ai={
            "enabled": True,
            "review": True,
            "provider": "openai",
            "transport": "cli",
            "transports": {"cli": {"max_cost_usd_advisory": 1.25}},
        },
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
        patch("lintro.cli_utils.commands.review.get_provider") as mock_get_provider,
        patch(
            "lintro.cli_utils.commands.review.run_review",
            return_value=_empty_result(),
        ),
        patch(
            "lintro.cli_utils.commands.review.render_review_output",
        ) as mock_render,
    ):
        mock_get_provider.return_value = MagicMock(
            model_name="gpt-4o",
            name="openai",
        )
        result = runner.invoke(cli, ["review", "--max-cost-usd", "0"])

    assert_that(result.exit_code).is_equal_to(0)
    provider_config = mock_get_provider.call_args.args[0]
    assert_that(provider_config.max_cost_usd).is_none()
    rendered = mock_render.call_args.kwargs["result"]
    assert_that(rendered.metadata.max_cost_usd).is_none()
    assert_that(rendered.metadata.max_cost_usd_source).is_equal_to("flag")


def test_review_profile_cap_provenance_is_config() -> None:
    """A YAML-only transport-profile cap is sourced as config, not default."""
    runner = CliRunner()
    mock_context = MagicMock()
    mock_context.changed_files = []
    mock_context.unified_diff = ""
    mock_config = MagicMock(
        ai={
            "enabled": True,
            "review": True,
            "provider": "openai",
            "transport": "cli",
            "transports": {"cli": {"max_cost_usd_advisory": 1.25}},
        },
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
        patch("lintro.cli_utils.commands.review.get_provider") as mock_get_provider,
        patch(
            "lintro.cli_utils.commands.review.run_review",
            return_value=_empty_result(),
        ),
        patch(
            "lintro.cli_utils.commands.review.render_review_output",
        ) as mock_render,
    ):
        mock_get_provider.return_value = MagicMock(
            model_name="gpt-4o",
            name="openai",
        )
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(0)
    rendered = mock_render.call_args.kwargs["result"]
    assert_that(rendered.metadata.max_cost_usd).is_equal_to(1.25)
    assert_that(rendered.metadata.max_cost_usd_source).is_equal_to("config")


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


def test_review_refuses_when_only_lint_enabled() -> None:
    """Lint-only config refuses `lintro review` naming the ai.review key."""
    runner = CliRunner()
    mock_config = MagicMock(
        ai=AIConfig(
            enabled=True,
            lint=True,
            review=False,
            transport=AITransport.API,
        ).model_dump(),
    )

    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
    ):
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("ai.review: true")


def test_review_runs_when_review_enabled_without_lint() -> None:
    """Review-only config (lint off) passes the entry gate and runs."""
    runner = CliRunner()
    mock_collect = MagicMock(
        return_value=MagicMock(changed_files=[], unified_diff=""),
    )
    patches = _mock_review_pipeline(mock_collect=mock_collect)
    review_config = MagicMock(
        ai=AIConfig(
            enabled=True,
            lint=False,
            review=True,
            provider=AIProvider.OPENAI,
            transport=AITransport.API,
        ).model_dump(),
    )
    review_config.review.depth = 1
    review_config.review.strictness = ReviewStrictness.BALANCED
    review_config.review.sensitivity = MagicMock()
    review_config.review.force_semantic_chunking = False
    review_config.review.checklist_display = ChecklistDisplay.OFF

    with (
        patches["require_ai"],
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=review_config,
        ),
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


def test_review_json_output_echoes_payload() -> None:
    """Review command echoes JSON when --output json is used."""
    runner = CliRunner()
    mock_context = MagicMock()
    mock_context.changed_files = []
    mock_context.unified_diff = ""
    mock_config = MagicMock(ai={"enabled": True, "provider": "openai"})
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
        ai=AIConfig(
            enabled=True,
            provider=AIProvider.OPENAI,
            transport=AITransport.API,
        ).model_dump(),
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


def test_review_passes_provider_and_model_overrides_to_provider() -> None:
    """--provider and --model override config when resolving the provider."""
    runner = CliRunner()
    mock_context = MagicMock()
    mock_context.changed_files = []
    mock_config = MagicMock(
        ai=AIConfig(
            enabled=True,
            provider=AIProvider.OPENAI,
            transport=AITransport.API,
        ).model_dump(),
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
            model_name="cursor-grok-4.6-high",
            name="cursor",
        )
        result = runner.invoke(
            cli,
            [
                "review",
                "--provider",
                "cursor",
                "--model",
                "cursor-grok-4.6-high",
                "--transport",
                "cli",
            ],
        )

    assert_that(result.exit_code).is_equal_to(0)
    provider_config = mock_get_provider.call_args.args[0]
    assert_that(provider_config.provider.value).is_equal_to("cursor")
    assert_that(provider_config.model).is_equal_to("cursor-grok-4.6-high")
    assert_that(provider_config.transport.value).is_equal_to("cli")


def test_review_stamps_resolved_transport_provenance_on_metadata() -> None:
    """The rendered result carries transport, auth_mode, and cost_basis.

    ``review_command`` overwrites the orchestrator metadata with the resolved
    transport profile (#1923); sticky state and the PR comment read these
    fields, so a regression here silently mislabels cost provenance.
    """
    runner = CliRunner()
    mock_context = MagicMock()
    mock_context.changed_files = []
    mock_context.unified_diff = ""
    mock_config = MagicMock(
        ai={
            "enabled": True,
            "review": True,
            "provider": "anthropic",
            "transport": "api",
        },
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
        patch("lintro.cli_utils.commands.review.get_provider") as mock_get_provider,
        patch(
            "lintro.cli_utils.commands.review.run_review",
            return_value=_empty_result(),
        ),
        patch(
            "lintro.cli_utils.commands.review.render_review_output",
        ) as mock_render,
    ):
        mock_get_provider.return_value = MagicMock(
            model_name="gpt-4o",
            name="openai",
        )
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(0)
    rendered = mock_render.call_args.kwargs["result"]
    assert_that(rendered.metadata.transport).is_equal_to("api")
    assert_that(rendered.metadata.auth_mode).is_equal_to("api_key")
    assert_that(rendered.metadata.cost_basis).is_equal_to("billed")
    assert_that(rendered.metadata.provider_source).is_equal_to("config")
    assert_that(rendered.metadata.transport_source).is_equal_to("config")
    assert_that(rendered.metadata.max_cost_usd).is_none()
    assert_that(rendered.metadata.max_cost_usd_source).is_equal_to("default")


def test_review_downgrades_billed_to_estimated_without_usage_counters() -> None:
    """An api run with locally estimated tokens must not claim ``billed``.

    The profile resolves BILLED before the run; when the orchestrator sets
    ``token_usage_estimated`` (provider returned no usage counters) the
    stamped basis is reconciled to ESTIMATED so sticky state and the PR
    comment stay honest (#1923).
    """
    from dataclasses import replace as dc_replace

    runner = CliRunner()
    mock_context = MagicMock()
    mock_context.changed_files = []
    mock_context.unified_diff = ""
    mock_config = MagicMock(
        ai=AIConfig(
            enabled=True,
            provider=AIProvider.OPENAI,
            transport=AITransport.API,
        ).model_dump(),
    )
    mock_config.review.depth = 1
    mock_config.review.strictness = ReviewStrictness.BALANCED
    mock_config.review.sensitivity = MagicMock()
    mock_config.review.force_semantic_chunking = False
    mock_config.review.checklist_display = ChecklistDisplay.OFF

    estimated_result = _empty_result()
    estimated_result = dc_replace(
        estimated_result,
        metadata=dc_replace(estimated_result.metadata, token_usage_estimated=True),
    )

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
        patch("lintro.cli_utils.commands.review.get_provider") as mock_get_provider,
        patch(
            "lintro.cli_utils.commands.review.run_review",
            return_value=estimated_result,
        ),
        patch(
            "lintro.cli_utils.commands.review.render_review_output",
        ) as mock_render,
    ):
        mock_get_provider.return_value = MagicMock(
            model_name="gpt-4o",
            name="openai",
        )
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(0)
    rendered = mock_render.call_args.kwargs["result"]
    assert_that(rendered.metadata.cost_basis).is_equal_to("estimated")


def test_review_exits_zero_without_p1_findings() -> None:
    """Review command exits 0 when no P1 findings exist."""
    runner = CliRunner()
    mock_context = MagicMock()
    mock_context.changed_files = []
    mock_context.unified_diff = ""
    mock_config = MagicMock(ai={"enabled": True, "provider": "openai"})
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
    mock_config: MagicMock | None = None,
) -> dict[str, Any]:
    """Return patched review dependencies for CliRunner mode wiring tests.

    Args:
        mock_collect: Replacement for the context-collection patch.
        mock_config: Replacement lintro config. Defaults to a minimal config
            with AI enabled and an explicit provider; pass one to exercise
            config-dependent wiring without duplicating the whole patch stack.

    Returns:
        Named patchers to enter around a ``CliRunner`` invocation.
    """
    mock_context = MagicMock()
    mock_context.changed_files = []
    mock_context.unified_diff = ""
    if mock_config is None:
        mock_config = MagicMock(ai={"enabled": True, "provider": "openai"})
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
            "exclude_globs": [],
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
            "exclude_globs": [],
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
            "exclude_globs": [],
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
    mock_config = MagicMock(ai={"enabled": True})

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
    assert_that(mock_collect.call_args.kwargs).is_equal_to(
        {
            "base": None,
            "uncommitted": False,
            "pr_number": 42,
            "repo": "owner/repo",
            "paths": None,
            "exclude_globs": [],
        },
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
    mock_config = MagicMock(ai={"enabled": True, "provider": "openai"})
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

    # Exit 2, not 1: no review was produced. Exit 1 stays reserved for a review
    # that ran and found P1 issues (#1826).
    assert_that(result.exit_code).is_equal_to(2)
    assert_that(result.output).contains("Review failed")
    assert_that(result.output).contains("chunk 3/6")
    assert_that(result.output).contains("api_timeout")
    assert_that(result.output).does_not_contain("Traceback")


def _write_agent_file(*, root: Path, name: str, text: str) -> None:
    """Write a custom review agent file into a workspace.

    Args:
        root: Workspace root.
        name: Markdown file name.
        text: File contents.
    """
    directory = root / ".lintro" / "review-agents"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


_AGENT_MARKDOWN = (
    "---\nname: no-raw-sql\ndescription: SQL via repositories only\n"
    "include: ['src/**/*.py']\n---\n\nFlag raw SQL.\n"
)


def _agent_mode_config(*, tmp_path: Path, mode: CustomAgentMode) -> MagicMock:
    """Build a review config mock scoped to a workspace and agent mode.

    Args:
        tmp_path: Workspace root holding ``.lintro/review-agents``.
        mode: Custom agent activation mode under test.

    Returns:
        The configured mock.
    """
    mock_config = MagicMock(ai={"enabled": True, "provider": "openai"})
    mock_config.config_path = str(tmp_path / ".lintro-config.yaml")
    mock_config.review.depth = 1
    mock_config.review.strictness = ReviewStrictness.BALANCED
    mock_config.review.sensitivity = MagicMock()
    mock_config.review.force_semantic_chunking = False
    mock_config.review.checklist_display = ChecklistDisplay.OFF
    mock_config.review.custom_agents = mode
    return mock_config


def test_review_help_shows_list_agents_flag() -> None:
    """Review help advertises the custom agent listing flag."""
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("--list-agents")


def test_review_list_agents_prints_discovered_agents(tmp_path: Path) -> None:
    """--list-agents prints discovered agents without needing a provider."""
    _write_agent_file(root=tmp_path, name="a.md", text=_AGENT_MARKDOWN)
    _write_agent_file(
        root=tmp_path,
        name="bad.md",
        text="---\nname: bad\n---\n\nbody\n",
    )
    runner = CliRunner()
    mock_config = MagicMock(ai={"enabled": True})
    mock_config.config_path = str(tmp_path / ".lintro-config.yaml")

    with (
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
        patch("lintro.cli_utils.commands.review.require_ai") as require_ai,
    ):
        result = runner.invoke(cli, ["review", "--list-agents"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("no-raw-sql (enabled)")
    assert_that(result.output).contains("include: src/**/*.py")
    assert_that(result.output).contains("Invalid agent files (skipped): 1")
    assert_that(require_ai.called).is_false()


def test_review_passes_discovered_agents_to_run_review(tmp_path: Path) -> None:
    """Discovered agents reach run_review with the built-in checklist on."""
    _write_agent_file(root=tmp_path, name="a.md", text=_AGENT_MARKDOWN)
    runner = CliRunner()
    patches = _mock_review_pipeline()
    mock_config = _agent_mode_config(tmp_path=tmp_path, mode=CustomAgentMode.ENABLED)

    with (
        patches["require_ai"],
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
        patches["collect_review_context"],
        patches["classify_changed_files"],
        patches["get_all_checklist_items"],
        patches["select_checklist_items"],
        patches["format_checklist_for_prompt"],
        patches["get_provider"],
        patch(
            "lintro.cli_utils.commands.review.run_review",
            return_value=_empty_result(),
        ) as run_review,
        patches["render_review_output"],
    ):
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(0)
    kwargs = run_review.call_args.kwargs
    assert_that([agent.name for agent in kwargs["custom_agents"]]).is_equal_to(
        ["no-raw-sql"],
    )
    assert_that(kwargs["run_builtin_checklist"]).is_true()


def test_review_custom_agents_disabled_skips_discovery(tmp_path: Path) -> None:
    """custom_agents: false skips discovery entirely."""
    _write_agent_file(root=tmp_path, name="a.md", text=_AGENT_MARKDOWN)
    runner = CliRunner()
    patches = _mock_review_pipeline()
    mock_config = _agent_mode_config(tmp_path=tmp_path, mode=CustomAgentMode.DISABLED)

    with (
        patches["require_ai"],
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
        patches["collect_review_context"],
        patches["classify_changed_files"],
        patches["get_all_checklist_items"],
        patches["select_checklist_items"],
        patches["format_checklist_for_prompt"],
        patches["get_provider"],
        patch(
            "lintro.cli_utils.commands.review.run_review",
            return_value=_empty_result(),
        ) as run_review,
        patches["render_review_output"],
    ):
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(0)
    kwargs = run_review.call_args.kwargs
    assert_that(kwargs["custom_agents"]).is_empty()
    assert_that(kwargs["run_builtin_checklist"]).is_true()


def test_review_custom_agents_only_disables_builtin_checklist(
    tmp_path: Path,
) -> None:
    """custom_agents: only turns the built-in checklist pass off."""
    _write_agent_file(root=tmp_path, name="a.md", text=_AGENT_MARKDOWN)
    runner = CliRunner()
    patches = _mock_review_pipeline()
    mock_config = _agent_mode_config(tmp_path=tmp_path, mode=CustomAgentMode.ONLY)

    with (
        patches["require_ai"],
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
        patches["collect_review_context"],
        patches["classify_changed_files"],
        patches["get_all_checklist_items"],
        patches["select_checklist_items"],
        patches["format_checklist_for_prompt"],
        patches["get_provider"],
        patch(
            "lintro.cli_utils.commands.review.run_review",
            return_value=_empty_result(),
        ) as run_review,
        patches["render_review_output"],
    ):
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(0)
    kwargs = run_review.call_args.kwargs
    assert_that(kwargs["run_builtin_checklist"]).is_false()
    assert_that(kwargs["custom_agents"]).is_length(1)


def test_review_custom_agents_only_with_no_valid_agents_errors(
    tmp_path: Path,
) -> None:
    """custom_agents: only with no discovered agents fails loudly.

    Otherwise the built-in checklist is skipped, no agents run, and the
    command reports a clean review with nothing actually checked.
    """
    runner = CliRunner()
    patches = _mock_review_pipeline()
    mock_config = _agent_mode_config(tmp_path=tmp_path, mode=CustomAgentMode.ONLY)

    with (
        patches["require_ai"],
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
        patches["collect_review_context"],
        patches["classify_changed_files"],
        patches["get_all_checklist_items"],
        patches["select_checklist_items"],
        patches["format_checklist_for_prompt"],
        patches["get_provider"],
        patch(
            "lintro.cli_utils.commands.review.run_review",
            return_value=_empty_result(),
        ) as run_review,
        patches["render_review_output"],
    ):
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(2)
    assert_that(str(result.output)).contains("no valid agents were found")
    assert_that(run_review.called).is_false()


# =============================================================================
# Advisory AI finders (#1308)
# =============================================================================


def _advisory_finding_result() -> ToolResult:
    """Build an advisory tool result carrying a single finding."""
    return ToolResult(
        name="idiom-review",
        success=False,
        issues_count=1,
        issues=[
            IdiomReviewIssue(
                file="a.py",
                line=3,
                message="prefer any()",
                code="idiom/python/prefer-any",
            ),
        ],
    )


def test_review_help_shows_advisory_flags() -> None:
    """Review help documents the advisory finder flags."""
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("--advisory-tools")
    assert_that(result.output).contains("--advisory-only")
    assert_that(result.output).contains("--fail-on-findings")


def _advisory_error_result() -> ToolResult:
    """Build an advisory tool result for a configuration/runtime failure."""
    return ToolResult(
        name="idiom-review",
        success=False,
        output=(
            "ai.provider is required when ai.lint or ai.review is enabled. "
            "Set it via `ai.provider` in config, LINTRO_AI_PROVIDER, or --provider. "
            "Accepted providers: anthropic, cursor, openai."
        ),
    )


def test_advisory_only_unset_provider_exits_two_before_tools() -> None:
    """--advisory-only fails closed on an unset provider before tools run."""
    from lintro.ai.review.error_contract import REVIEW_ERROR_EXIT_CODE

    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=MagicMock(ai={"enabled": True, "review": True}),
        ),
        patch(
            "lintro.cli_utils.commands.review.apply_cli_overrides",
            lambda _resolved, **_kwargs: AIConfig.resolve_from_mapping(
                {"enabled": True, "review": True},
            ),
        ),
        patch(
            "lintro.cli_utils.commands.review.run_advisory_tools",
        ) as run_advisory,
    ):
        result = runner.invoke(
            cli,
            ["review", "--advisory-only", "--output", "json"],
        )

    assert_that(result.exit_code).is_equal_to(REVIEW_ERROR_EXIT_CODE)
    payload = json.loads(result.output[result.output.index("{") :])
    assert_that(payload["error"]["kind"]).is_equal_to("provider_unavailable")
    assert_that(payload["error"]["message"]).contains("--provider")
    assert_that(run_advisory.called).is_false()


def test_advisory_only_exits_zero_with_findings() -> None:
    """Advisory findings are advisory: exit 0 by default."""
    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.run_advisory_tools",
            return_value=[_advisory_finding_result()],
        ),
    ):
        result = runner.invoke(cli, ["review", "--advisory-only"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("idiom-review")


def test_advisory_only_fail_on_findings_exits_one() -> None:
    """--fail-on-findings turns advisory findings into a failure."""
    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.run_advisory_tools",
            return_value=[_advisory_finding_result()],
        ),
    ):
        result = runner.invoke(
            cli,
            ["review", "--advisory-only", "--fail-on-findings"],
        )

    assert_that(result.exit_code).is_equal_to(1)


def test_advisory_only_json_output() -> None:
    """--advisory-only --output json emits an advisory document."""
    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.run_advisory_tools",
            return_value=[_advisory_finding_result()],
        ),
    ):
        result = runner.invoke(
            cli,
            ["review", "--advisory-only", "--output", "json"],
        )

    assert_that(result.exit_code).is_equal_to(0)
    payload = json.loads(result.output)
    assert_that(payload["advisory"]).is_length(1)
    assert_that(payload["advisory"][0]["tool"]).is_equal_to("idiom-review")
    assert_that(payload["advisory"][0]["success"]).is_false()


def test_advisory_only_errored_tool_exits_two() -> None:
    """An advisory tool that failed to run is not a finding and exits 2."""
    from lintro.ai.review.error_contract import REVIEW_ERROR_EXIT_CODE

    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.run_advisory_tools",
            return_value=[_advisory_error_result()],
        ),
    ):
        result = runner.invoke(
            cli,
            ["review", "--advisory-only", "--output", "json"],
        )

    assert_that(result.exit_code).is_equal_to(REVIEW_ERROR_EXIT_CODE)
    payload = json.loads(result.output[result.output.index("{") :])
    assert_that(payload["error"]["kind"]).is_equal_to("provider_unavailable")
    assert_that(payload["error"]["message"]).contains("`ai.provider` in config")
    assert_that(payload).does_not_contain_key("advisory")
    assert_that(payload).does_not_contain_key("findings")


def test_advisory_only_rejects_diff_flags() -> None:
    """--advisory-only cannot be combined with diff-review flags."""
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--advisory-only", "--uncommitted"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("--advisory-only")


def test_advisory_only_with_no_tools_errors() -> None:
    """Asking for advisory-only while disabling every tool is a usage error."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.review.require_ai"):
        result = runner.invoke(
            cli,
            ["review", "--advisory-only", "--advisory-tools", "none"],
        )

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("would run nothing")


def test_advisory_tools_none_skips_advisory_in_full_review() -> None:
    """--advisory-tools none runs the diff review without advisory tools."""
    runner = CliRunner()
    patches = _mock_review_pipeline()

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
            "lintro.cli_utils.commands.review.run_advisory_tools",
        ) as run_advisory,
    ):
        result = runner.invoke(cli, ["review", "--advisory-tools", "none"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(run_advisory.call_args.kwargs["tool_names"]).is_empty()


def test_unknown_advisory_tool_is_a_usage_error() -> None:
    """An unknown advisory tool name fails as a usage error."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.review.require_ai"):
        result = runner.invoke(
            cli,
            ["review", "--advisory-only", "--advisory-tools", "nope"],
        )

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Unknown advisory tool")


def test_deterministic_tool_rejected_by_review() -> None:
    """Naming a deterministic tool on review points back at chk."""
    runner = CliRunner()
    with patch("lintro.cli_utils.commands.review.require_ai"):
        result = runner.invoke(
            cli,
            ["review", "--advisory-only", "--advisory-tools", "ruff"],
        )

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("lintro chk --tools ruff")


def test_merge_advisory_into_json_adds_key() -> None:
    """Advisory results are added to the review JSON as an additive key."""
    merged = _merge_advisory_into_json(
        review_output=json.dumps({"summary": "ok"}),
        advisory_results=[_advisory_finding_result()],
    )

    document = json.loads(str(merged))
    assert_that(document["summary"]).is_equal_to("ok")
    assert_that(document["advisory"][0]["issues_count"]).is_equal_to(1)


def test_merge_advisory_into_json_leaves_non_json_untouched() -> None:
    """A non-JSON payload is returned verbatim rather than corrupted."""
    merged = _merge_advisory_into_json(
        review_output="not json",
        advisory_results=[_advisory_finding_result()],
    )

    assert_that(merged).is_equal_to("not json")


def test_merge_advisory_into_json_without_advisory_results() -> None:
    """No advisory results means the review document is unchanged."""
    merged = _merge_advisory_into_json(
        review_output=json.dumps({"summary": "ok"}),
        advisory_results=[],
    )

    assert_that(json.loads(str(merged))).does_not_contain_key("advisory")


def test_full_review_renders_advisory_block_after_review_output() -> None:
    """Terminal output shows advisory findings under the diff review."""
    runner = CliRunner()
    patches = _mock_review_pipeline()

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
        patch(
            "lintro.cli_utils.commands.review.render_review_output",
            return_value="REVIEW BODY",
        ),
        patch(
            "lintro.cli_utils.commands.review.run_advisory_tools",
            return_value=[_advisory_finding_result()],
        ),
    ):
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("REVIEW BODY")
    assert_that(result.output.index("REVIEW BODY")).is_less_than(
        result.output.index("Advisory: idiom-review"),
    )


def test_full_review_json_merges_advisory_key() -> None:
    """JSON output carries advisory findings under an additive key."""
    runner = CliRunner()
    patches = _mock_review_pipeline()

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
        patch(
            "lintro.cli_utils.commands.review.render_review_output",
            return_value=json.dumps({"summary": "ok"}),
        ),
        patch(
            "lintro.cli_utils.commands.review.run_advisory_tools",
            return_value=[_advisory_finding_result()],
        ),
    ):
        result = runner.invoke(cli, ["review", "--output", "json"])

    assert_that(result.exit_code).is_equal_to(0)
    payload = json.loads(result.output)
    assert_that(payload["summary"]).is_equal_to("ok")
    assert_that(payload["advisory"][0]["tool"]).is_equal_to("idiom-review")


def test_full_review_errored_advisory_exits_two() -> None:
    """Advisory execution failure emits the error contract, not a review."""
    from lintro.ai.review.error_contract import REVIEW_ERROR_EXIT_CODE

    runner = CliRunner()
    patches = _mock_review_pipeline()

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
        patch(
            "lintro.cli_utils.commands.review.render_review_output",
            return_value=json.dumps({"summary": "ok", "findings": []}),
        ) as mock_render,
        patch(
            "lintro.cli_utils.commands.review.run_advisory_tools",
            return_value=[_advisory_error_result()],
        ),
        patch(
            "lintro.ai.review.github.post_review_to_github",
            return_value=True,
        ) as mock_post,
    ):
        result = runner.invoke(cli, ["review", "--output", "json"])

    assert_that(result.exit_code).is_equal_to(REVIEW_ERROR_EXIT_CODE)
    payload = json.loads(result.output[result.output.index("{") :])
    assert_that(payload["error"]["kind"]).is_equal_to("provider_unavailable")
    assert_that(payload).does_not_contain_key("findings")
    assert_that(payload).does_not_contain_key("summary")
    assert_that(mock_render.called).is_false()
    assert_that(mock_post.called).is_false()


def test_cli_overrides_lists_only_explicit_flags() -> None:
    """Only options the caller actually passed appear as overrides."""
    overrides = _cli_overrides(
        depth=None,
        strictness=None,
        transport="cli",
        provider=None,
        model=None,
        max_cost_usd=None,
        timeout=600.0,
        context_window=None,
        semantic_chunks=False,
        paths=None,
    )

    assert_that(overrides).is_equal_to(["--transport cli", "--timeout 600"])


def test_describe_config_source_names_the_file_without_its_path() -> None:
    """An absolute CI path must not leak into a public PR comment."""
    described = _describe_config_source(
        config_path="/home/runner/work/repo/repo/.lintro-config.yaml",
        overrides=["--timeout 600"],
    )

    assert_that(described).is_equal_to(
        "`.lintro-config.yaml` + CLI overrides (--timeout 600)",
    )


def test_describe_config_source_falls_back_to_defaults() -> None:
    """With no config file the note says so rather than rendering an empty name."""
    assert_that(
        _describe_config_source(config_path=None, overrides=[]),
    ).is_equal_to("built-in defaults")


def test_review_post_reports_config_source_and_transport() -> None:
    """--post hands the posting layer the config file and the CLI overrides.

    Driven through the CLI rather than the private helpers, so a rename of
    those helpers cannot pass while the wiring itself has broken.
    """
    runner = CliRunner()
    mock_config = MagicMock(
        ai=AIConfig(
            enabled=True,
            provider=AIProvider.OPENAI,
            transport=AITransport.API,
        ).model_dump(),
    )
    mock_config.config_path = "/home/runner/work/repo/repo/.lintro-config.yaml"
    mock_config.review.depth = 1
    mock_config.review.strictness = ReviewStrictness.BALANCED
    mock_config.review.sensitivity = MagicMock()
    mock_config.review.force_semantic_chunking = False
    mock_config.review.checklist_display = ChecklistDisplay.OFF
    patches = _mock_review_pipeline(mock_config=mock_config)

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
            "lintro.ai.review.github.post_review_to_github",
            return_value=True,
        ) as mock_post,
    ):
        result = runner.invoke(
            cli,
            [
                "review",
                "--post",
                "--pr",
                "7",
                "--repo",
                "owner/name",
                "--timeout",
                "600",
            ],
        )

    assert_that(result.exit_code).is_equal_to(0)
    kwargs = mock_post.call_args.kwargs
    assert_that(kwargs["config_source"]).is_equal_to(
        "`.lintro-config.yaml` + CLI overrides (--timeout 600)",
    )
    assert_that(kwargs["transport"]).is_equal_to(str(AITransport.API))
