"""Tests for the env-var and CLI-flag AI config override layer (#1970)."""

from __future__ import annotations

import os

import pytest
from assertpy import assert_that
from rich.console import Console

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport, ConfigSource
from lintro.ai.exceptions import AIConfigOverrideError
from lintro.ai.provider_enum import AIProvider
from lintro.ai.resolved_ai_config import format_sourced_value
from lintro.ai.review.display import render_review_terminal
from lintro.ai.review.github_render import format_run_mechanics
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.transport import apply_cli_overrides
from lintro.config.lintro_config import LintroConfig


def _mapping(**overrides: object) -> dict[str, object]:
    """Build a raw ``ai:`` mapping with explicit keys only.

    Args:
        **overrides: Keys present in the committed config.

    Returns:
        Mapping suitable for :meth:`AIConfig.resolve_from_mapping`.
    """
    return dict(overrides)


def test_each_env_var_overrides_its_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each ``LINTRO_AI_*`` variable maps onto exactly one field."""
    monkeypatch.setenv("LINTRO_AI_PROVIDER", "cursor")
    monkeypatch.setenv("LINTRO_AI_MODEL", "cursor-grok-4.6-high")
    monkeypatch.setenv("LINTRO_AI_TRANSPORT", "cli")
    monkeypatch.setenv("LINTRO_AI_ENABLED", "1")

    resolved = AIConfig.resolve_from_mapping(
        _mapping(provider="anthropic", model="claude-sonnet", transport="api"),
    )

    assert_that(resolved.config.provider).is_equal_to(AIProvider.CURSOR)
    assert_that(resolved.config.model).is_equal_to("cursor-grok-4.6-high")
    assert_that(resolved.config.transport).is_equal_to(AITransport.CLI)
    assert_that(resolved.config.enabled).is_true()
    assert_that(resolved.source_of("provider")).is_equal_to(ConfigSource.ENV)
    assert_that(resolved.source_of("model")).is_equal_to(ConfigSource.ENV)
    assert_that(resolved.source_of("transport")).is_equal_to(ConfigSource.ENV)
    assert_that(resolved.source_of("enabled")).is_equal_to(ConfigSource.ENV)


def test_flag_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI flags win over environment variables."""
    monkeypatch.setenv("LINTRO_AI_PROVIDER", "openai")
    monkeypatch.setenv("LINTRO_AI_MODEL", "env-model")
    monkeypatch.setenv("LINTRO_AI_TRANSPORT", "api")

    resolved = apply_cli_overrides(
        AIConfig.resolve_from_mapping(_mapping(provider="anthropic")),
        provider="cursor",
        model="flag-model",
        transport="cli",
    )

    assert_that(resolved.config.provider).is_equal_to(AIProvider.CURSOR)
    assert_that(resolved.config.model).is_equal_to("flag-model")
    assert_that(resolved.config.transport).is_equal_to(AITransport.CLI)
    assert_that(resolved.source_of("provider")).is_equal_to(ConfigSource.FLAG)
    assert_that(resolved.source_of("model")).is_equal_to(ConfigSource.FLAG)
    assert_that(resolved.source_of("transport")).is_equal_to(ConfigSource.FLAG)


def test_env_beats_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment values win over the committed ``ai:`` mapping."""
    monkeypatch.setenv("LINTRO_AI_PROVIDER", "openai")

    resolved = AIConfig.resolve_from_mapping(_mapping(provider="anthropic"))

    assert_that(resolved.config.provider).is_equal_to(AIProvider.OPENAI)
    assert_that(resolved.source_of("provider")).is_equal_to(ConfigSource.ENV)


def test_unset_env_falls_through_to_config() -> None:
    """An absent env layer leaves the mapping (or default) in place."""
    resolved = AIConfig.resolve_from_mapping(
        _mapping(provider="openai", model="gpt-4o", transport="cli", enabled=True),
    )

    assert_that(resolved.config.provider).is_equal_to(AIProvider.OPENAI)
    assert_that(resolved.config.model).is_equal_to("gpt-4o")
    assert_that(resolved.config.transport).is_equal_to(AITransport.CLI)
    assert_that(resolved.source_of("provider")).is_equal_to(ConfigSource.CONFIG)
    assert_that(resolved.source_of("model")).is_equal_to(ConfigSource.CONFIG)
    assert_that(resolved.source_of("transport")).is_equal_to(ConfigSource.CONFIG)
    assert_that(resolved.source_of("enabled")).is_equal_to(ConfigSource.CONFIG)


def test_empty_mapping_uses_built_in_defaults() -> None:
    """Omitted fields record ``default`` provenance."""
    resolved = AIConfig.resolve_from_mapping(None)

    assert_that(resolved.config).is_equal_to(AIConfig())
    assert_that(resolved.source_of("provider")).is_equal_to(ConfigSource.DEFAULT)
    assert_that(resolved.source_of("model")).is_equal_to(ConfigSource.DEFAULT)
    assert_that(resolved.source_of("transport")).is_equal_to(ConfigSource.DEFAULT)
    assert_that(resolved.source_of("enabled")).is_equal_to(ConfigSource.DEFAULT)


def test_invalid_provider_env_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo'd provider fails loudly and does not fall back."""
    monkeypatch.setenv("LINTRO_AI_PROVIDER", "cursur")

    with pytest.raises(AIConfigOverrideError) as exc_info:
        AIConfig.resolve_from_mapping(_mapping(provider="anthropic"))

    message = str(exc_info.value)
    assert_that(message).contains("LINTRO_AI_PROVIDER='cursur'")
    assert_that(message).contains("anthropic")
    assert_that(message).contains("openai")
    assert_that(message).contains("cursor")
    assert_that(message).does_not_contain("Traceback")


def test_invalid_transport_env_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo'd transport fails loudly and does not fall back."""
    monkeypatch.setenv("LINTRO_AI_TRANSPORT", "ssh")

    with pytest.raises(AIConfigOverrideError) as exc_info:
        AIConfig.resolve_from_mapping(_mapping(transport="api"))

    message = str(exc_info.value)
    assert_that(message).contains("LINTRO_AI_TRANSPORT='ssh'")
    assert_that(message).contains("api")
    assert_that(message).contains("cli")


def test_invalid_enabled_env_names_accepted_spellings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LINTRO_AI_ENABLED`` rejects values outside 1/0/true/false."""
    monkeypatch.setenv("LINTRO_AI_ENABLED", "yesmaybe")

    with pytest.raises(AIConfigOverrideError) as exc_info:
        AIConfig.resolve_from_mapping({})

    assert_that(str(exc_info.value)).contains("LINTRO_AI_ENABLED='yesmaybe'")
    assert_that(str(exc_info.value)).contains("1, 0, true, false")


def test_enabled_zero_disables_globally(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LINTRO_AI_ENABLED=0`` is a kill switch across lint and review."""
    monkeypatch.setenv("LINTRO_AI_ENABLED", "0")

    resolved = AIConfig.resolve_from_mapping(
        _mapping(enabled=True, lint=True, review=True),
    )

    assert_that(resolved.config.enabled).is_false()
    assert_that(resolved.config.lint_enabled).is_false()
    assert_that(resolved.config.review_enabled).is_false()
    assert_that(resolved.source_of("enabled")).is_equal_to(ConfigSource.ENV)


def test_enabled_one_does_not_imply_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LINTRO_AI_ENABLED=1`` does not turn on ``ai.review`` or ``ai.lint``."""
    monkeypatch.setenv("LINTRO_AI_ENABLED", "1")

    resolved = AIConfig.resolve_from_mapping({})

    assert_that(resolved.config.enabled).is_true()
    assert_that(resolved.config.lint).is_false()
    assert_that(resolved.config.review).is_false()
    assert_that(resolved.config.review_enabled).is_false()
    assert_that(resolved.config.lint_enabled).is_false()


def test_max_cost_usd_has_no_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spend-cap env var is ignored; the committed cap cannot be raised."""
    monkeypatch.setenv("LINTRO_AI_MAX_COST_USD", "99.0")

    resolved = AIConfig.resolve_from_mapping(_mapping(max_cost_usd=0.5))

    assert_that(resolved.config.max_cost_usd).is_equal_to(0.5)
    assert_that(os.environ.get("LINTRO_AI_MAX_COST_USD")).is_equal_to("99.0")


def test_whitespace_only_env_is_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank env values do not overlay the mapping."""
    monkeypatch.setenv("LINTRO_AI_PROVIDER", "  ")
    monkeypatch.setenv("LINTRO_AI_MODEL", "")

    resolved = AIConfig.resolve_from_mapping(_mapping(provider="anthropic"))

    assert_that(resolved.config.provider).is_equal_to(AIProvider.ANTHROPIC)
    assert_that(resolved.source_of("provider")).is_equal_to(ConfigSource.CONFIG)
    assert_that(resolved.source_of("model")).is_equal_to(ConfigSource.DEFAULT)


def test_from_mapping_returns_the_resolved_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``from_mapping`` applies env overlays and returns the effective config."""
    monkeypatch.setenv("LINTRO_AI_PROVIDER", "cursor")

    config = AIConfig.from_mapping(_mapping(provider="anthropic"))

    assert_that(config.provider).is_equal_to(AIProvider.CURSOR)


def test_resolve_ai_config_facade_applies_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interface facade honors the env layer through ``from_mapping``."""
    from lintro.ai.interface import resolve_ai_config

    monkeypatch.setenv("LINTRO_AI_TRANSPORT", "cli")
    config = resolve_ai_config(LintroConfig(ai={"transport": "api"}))

    assert_that(config.transport).is_equal_to(AITransport.CLI)


def test_omitted_flags_leave_env_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``apply_cli_overrides`` is a no-op when every flag is omitted."""
    monkeypatch.setenv("LINTRO_AI_PROVIDER", "openai")
    resolved = apply_cli_overrides(
        AIConfig.resolve_from_mapping(_mapping(provider="anthropic")),
    )

    assert_that(resolved.config.provider).is_equal_to(AIProvider.OPENAI)
    assert_that(resolved.source_of("provider")).is_equal_to(ConfigSource.ENV)


def test_format_sourced_value_annotates_known_sources() -> None:
    """Display helper appends the source label when one is present."""
    assert_that(format_sourced_value("cursor", ConfigSource.ENV)).is_equal_to(
        "cursor (env)",
    )
    assert_that(format_sourced_value("cli", "flag")).is_equal_to("cli (flag)")
    assert_that(format_sourced_value("anthropic", None)).is_equal_to("anthropic")
    assert_that(format_sourced_value("anthropic", "")).is_equal_to("anthropic")


def test_status_annotates_env_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-execution status shows env provenance for provider/model/transport."""
    from lintro.ai.display.status import render_ai_status

    monkeypatch.setattr(
        "lintro.ai.availability.is_provider_available",
        lambda _provider: True,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LINTRO_AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    lines = render_ai_status(
        ai_config={"enabled": True, "provider": "anthropic", "transport": "api"},
        is_ci=False,
    )

    assert_that(lines).contains("  provider: openai (env)")
    assert_that("".join(lines)).contains("transport: api (config)")


def test_status_marks_enabled_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LINTRO_AI_ENABLED=0`` is visible on the disabled status line."""
    from lintro.ai.display.status import render_ai_status

    monkeypatch.setenv("LINTRO_AI_ENABLED", "0")

    lines = render_ai_status(
        ai_config={"enabled": True, "review": True},
        is_ci=False,
    )

    assert_that(lines).is_equal_to(["[dim]disabled (env)[/dim]"])


def _review_result_with_sources() -> ReviewResult:
    """Build a review result whose metadata carries field provenance.

    Returns:
        A result suitable for terminal and PR-comment rendering tests.
    """
    return ReviewResult(
        metadata=ReviewMetadata(
            model="cursor-grok-4.6-high",
            provider="cursor",
            context_window=128_000,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=1,
            files_total=1,
            checklist_items=0,
            transport="cli",
            provider_source="env",
            model_source="flag",
            transport_source="config",
        ),
        summary="Safe to merge.",
        checklist=(),
        findings=(),
    )


def test_terminal_review_annotates_provider_model_transport() -> None:
    """Terminal output shows provider, model, and transport with sources."""
    console = Console(record=True)
    render_review_terminal(result=_review_result_with_sources(), console=console)
    text = console.export_text()

    assert_that(text).contains("Model: cursor-grok-4.6-high (flag)")
    assert_that(text).contains("Provider: cursor (env)")
    assert_that(text).contains("Transport: cli (config)")


def test_pr_comment_mechanics_annotates_sources() -> None:
    """Posted PR comment mechanics name the source of each override field."""
    mechanics = format_run_mechanics(metadata=_review_result_with_sources().metadata)

    assert_that(mechanics).contains("**Model:** `cursor-grok-4.6-high` (flag)")
    assert_that(mechanics).contains("**Provider:** `cursor` (env)")
    assert_that(mechanics).contains("**Transport:** `cli` (config)")
