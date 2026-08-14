"""Tests for the env-var and CLI-flag AI config override layer (#1970, #2024)."""

from __future__ import annotations

import pytest
from assertpy import assert_that
from rich.console import Console

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport, ConfigSource
from lintro.ai.exceptions import AIConfigOverrideError
from lintro.ai.provider_enum import AIProvider
from lintro.ai.resolved_ai_config import format_max_cost_label, format_sourced_value
from lintro.ai.review.display import render_review_terminal
from lintro.ai.review.github_render import format_run_mechanics
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.transport import (
    apply_cli_overrides,
    apply_resolved_transport,
    resolve_max_cost_with_source,
)
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
    monkeypatch.setenv("LINTRO_AI_MAX_COST_USD", "2.5")

    resolved = AIConfig.resolve_from_mapping(
        _mapping(provider="anthropic", model="claude-sonnet", transport="api"),
    )

    assert_that(resolved.config.provider).is_equal_to(AIProvider.CURSOR)
    assert_that(resolved.config.model).is_equal_to("cursor-grok-4.6-high")
    assert_that(resolved.config.transport).is_equal_to(AITransport.CLI)
    assert_that(resolved.config.enabled).is_true()
    assert_that(resolved.config.max_cost_usd).is_equal_to(2.5)
    assert_that(resolved.source_of("provider")).is_equal_to(ConfigSource.ENV)
    assert_that(resolved.source_of("model")).is_equal_to(ConfigSource.ENV)
    assert_that(resolved.source_of("transport")).is_equal_to(ConfigSource.ENV)
    assert_that(resolved.source_of("enabled")).is_equal_to(ConfigSource.ENV)
    assert_that(resolved.source_of("max_cost_usd")).is_equal_to(ConfigSource.ENV)


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
    assert_that(resolved.source_of("max_cost_usd")).is_equal_to(ConfigSource.DEFAULT)
    assert_that(resolved.config.max_cost_usd).is_none()


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


def test_max_cost_usd_env_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LINTRO_AI_MAX_COST_USD`` raises the committed cap (#2024)."""
    monkeypatch.setenv("LINTRO_AI_MAX_COST_USD", "99.0")

    resolved = AIConfig.resolve_from_mapping(_mapping(max_cost_usd=0.5))

    assert_that(resolved.config.max_cost_usd).is_equal_to(99.0)
    assert_that(resolved.source_of("max_cost_usd")).is_equal_to(ConfigSource.ENV)


def test_max_cost_usd_flag_overrides_config() -> None:
    """``--max-cost-usd`` overlays the committed cap (#2024)."""
    resolved = apply_cli_overrides(
        AIConfig.resolve_from_mapping(_mapping(max_cost_usd=0.5)),
        max_cost_usd=3.25,
    )

    assert_that(resolved.config.max_cost_usd).is_equal_to(3.25)
    assert_that(resolved.source_of("max_cost_usd")).is_equal_to(ConfigSource.FLAG)


def test_max_cost_usd_flag_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cost-cap flag wins over the env var (#2024)."""
    monkeypatch.setenv("LINTRO_AI_MAX_COST_USD", "1.0")

    resolved = apply_cli_overrides(
        AIConfig.resolve_from_mapping(_mapping(max_cost_usd=0.5)),
        max_cost_usd=5.0,
    )

    assert_that(resolved.config.max_cost_usd).is_equal_to(5.0)
    assert_that(resolved.source_of("max_cost_usd")).is_equal_to(ConfigSource.FLAG)


def test_max_cost_usd_zero_is_uncapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Literal ``0`` lifts the ceiling to ``None``, matching CostBudget (#2024)."""
    monkeypatch.setenv("LINTRO_AI_MAX_COST_USD", "0")

    from_env = AIConfig.resolve_from_mapping(_mapping(max_cost_usd=0.5))
    from_flag = apply_cli_overrides(
        AIConfig.resolve_from_mapping(_mapping(max_cost_usd=0.5)),
        max_cost_usd=0.0,
    )

    assert_that(from_env.config.max_cost_usd).is_none()
    assert_that(from_env.source_of("max_cost_usd")).is_equal_to(ConfigSource.ENV)
    assert_that(from_flag.config.max_cost_usd).is_none()
    assert_that(from_flag.source_of("max_cost_usd")).is_equal_to(ConfigSource.FLAG)


def test_yaml_zero_is_a_zero_dollar_cap_not_uncapped() -> None:
    """Committed YAML ``0`` is a $0 cap; only overlay ``0`` is uncapped (#2024)."""
    resolved = AIConfig.resolve_from_mapping(_mapping(max_cost_usd=0))

    assert_that(resolved.config.max_cost_usd).is_equal_to(0.0)
    assert_that(resolved.source_of("max_cost_usd")).is_equal_to(ConfigSource.CONFIG)


def test_max_cost_usd_overlay_beats_transport_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag/env overlays beat YAML transport-profile caps (#2024)."""
    mapping = {
        "max_cost_usd": 0.5,
        "transport": "cli",
        "transports": {"cli": {"max_cost_usd_advisory": 1.25}},
    }
    monkeypatch.setenv("LINTRO_AI_MAX_COST_USD", "0")

    from_env = AIConfig.resolve_from_mapping(mapping)
    applied_env = apply_resolved_transport(from_env.config)

    assert_that(applied_env.max_cost_usd).is_none()
    assert_that(from_env.source_of("max_cost_usd")).is_equal_to(ConfigSource.ENV)

    monkeypatch.delenv("LINTRO_AI_MAX_COST_USD")
    from_flag = apply_cli_overrides(
        AIConfig.resolve_from_mapping(
            {
                "max_cost_usd": 0.5,
                "transport": "cli",
                "transports": {"cli": {"max_cost_usd_advisory": 1.25}},
            },
        ),
        max_cost_usd=0,
    )
    applied_flag = apply_resolved_transport(from_flag.config)

    assert_that(applied_flag.max_cost_usd).is_none()
    assert_that(from_flag.source_of("max_cost_usd")).is_equal_to(ConfigSource.FLAG)


def test_max_cost_usd_overlay_raises_profile_cap() -> None:
    """A positive overlay replaces the profile cap, not only the legacy scalar."""
    resolved = apply_cli_overrides(
        AIConfig.resolve_from_mapping(
            {
                "transport": "api",
                "transports": {"api": {"max_cost_usd": 1.25}},
            },
        ),
        max_cost_usd=9.0,
    )
    applied = apply_resolved_transport(resolved.config)

    assert_that(applied.max_cost_usd).is_equal_to(9.0)
    assert_that(resolved.source_of("max_cost_usd")).is_equal_to(ConfigSource.FLAG)


def test_profile_only_cap_is_config_provenance() -> None:
    """A YAML transport-profile cap is ``config``, not ``default`` (#2024)."""
    resolved = AIConfig.resolve_from_mapping(
        {
            "transport": "cli",
            "transports": {"cli": {"max_cost_usd_advisory": 1.25}},
        },
    )
    cap, source = resolve_max_cost_with_source(resolved)

    assert_that(resolved.source_of("max_cost_usd")).is_equal_to(ConfigSource.DEFAULT)
    assert_that(cap).is_equal_to(1.25)
    assert_that(source).is_equal_to(ConfigSource.CONFIG)


def test_flag_overlay_provenance_beats_profile_cap() -> None:
    """Flag provenance is kept when the overlay lifts a profile cap."""
    resolved = apply_cli_overrides(
        AIConfig.resolve_from_mapping(
            {
                "transport": "api",
                "transports": {"api": {"max_cost_usd": 1.25}},
            },
        ),
        max_cost_usd=0,
    )
    cap, source = resolve_max_cost_with_source(resolved)

    assert_that(cap).is_none()
    assert_that(source).is_equal_to(ConfigSource.FLAG)


def test_invalid_max_cost_usd_env_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-numeric cost cap names the variable and accepted values (#2024)."""
    monkeypatch.setenv("LINTRO_AI_MAX_COST_USD", "plenty")

    with pytest.raises(AIConfigOverrideError) as exc_info:
        AIConfig.resolve_from_mapping(_mapping(max_cost_usd=0.5))

    message = str(exc_info.value)
    assert_that(message).contains("LINTRO_AI_MAX_COST_USD='plenty'")
    assert_that(message).contains("0 for uncapped")
    assert_that(message).does_not_contain("Traceback")


def test_negative_max_cost_usd_fails_loud() -> None:
    """A negative ``--max-cost-usd`` is rejected rather than stored (#2024)."""
    with pytest.raises(AIConfigOverrideError) as exc_info:
        apply_cli_overrides(
            AIConfig.resolve_from_mapping(_mapping(max_cost_usd=0.5)),
            max_cost_usd=-1.0,
        )

    message = str(exc_info.value)
    assert_that(message).contains("--max-cost-usd=-1.0")
    assert_that(message).contains("0 for uncapped")


def test_nonnumeric_max_cost_usd_flag_fails_loud() -> None:
    """A non-numeric ``--max-cost-usd`` uses the overlay error, not Click (#2024)."""
    with pytest.raises(AIConfigOverrideError) as exc_info:
        apply_cli_overrides(
            AIConfig.resolve_from_mapping(_mapping(max_cost_usd=0.5)),
            max_cost_usd="plenty",
        )

    message = str(exc_info.value)
    assert_that(message).contains("--max-cost-usd='plenty'")
    assert_that(message).contains("0 for uncapped")


def test_nonfinite_max_cost_usd_fails_loud() -> None:
    """NaN and inf are rejected rather than stored as a cap (#2024)."""
    for raw in ("nan", "inf", "-inf"):
        with pytest.raises(AIConfigOverrideError) as exc_info:
            apply_cli_overrides(
                AIConfig.resolve_from_mapping(_mapping(max_cost_usd=0.5)),
                max_cost_usd=raw,
            )
        message = str(exc_info.value)
        assert_that(message).contains(f"--max-cost-usd='{raw}'")
        assert_that(message).contains("0 for uncapped")


def test_whitespace_max_cost_usd_env_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only ``LINTRO_AI_MAX_COST_USD`` leaves the mapping (#2024)."""
    monkeypatch.setenv("LINTRO_AI_MAX_COST_USD", "  ")

    resolved = AIConfig.resolve_from_mapping(_mapping(max_cost_usd=0.5))

    assert_that(resolved.config.max_cost_usd).is_equal_to(0.5)
    assert_that(resolved.source_of("max_cost_usd")).is_equal_to(ConfigSource.CONFIG)


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
    assert_that(
        format_max_cost_label(max_cost_usd=None, source=ConfigSource.ENV),
    ).is_equal_to("uncapped (env)")
    assert_that(
        format_max_cost_label(max_cost_usd=1.5, source="flag"),
    ).is_equal_to("$1.50 (flag)")


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
    assert_that(lines).contains("  max_cost_usd: uncapped (default)")


def test_status_annotates_env_max_cost_usd(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-execution status shows env provenance for the cost cap (#2024)."""
    from lintro.ai.display.status import render_ai_status

    monkeypatch.setattr(
        "lintro.ai.availability.is_provider_available",
        lambda _provider: True,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LINTRO_AI_MAX_COST_USD", "2.5")

    lines = render_ai_status(
        ai_config={"enabled": True, "provider": "anthropic", "transport": "api"},
        is_ci=False,
    )

    assert_that(lines).contains("  max_cost_usd: $2.50 (env)")


def test_status_annotates_profile_cap_as_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A YAML profile cap is shown as config, not uncapped default (#2024)."""
    from lintro.ai.display.status import render_ai_status

    monkeypatch.setattr(
        "lintro.ai.availability.is_provider_available",
        lambda _provider: True,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    lines = render_ai_status(
        ai_config={
            "enabled": True,
            "provider": "anthropic",
            "transport": "cli",
            "transports": {"cli": {"max_cost_usd_advisory": 1.25}},
        },
        is_ci=False,
    )

    assert_that(lines).contains("  max_cost_usd: $1.25 (config)")
    assert_that(lines).does_not_contain("  max_cost_usd: uncapped (default)")


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
            max_cost_usd=None,
            max_cost_usd_source="env",
        ),
        summary="Safe to merge.",
        checklist=(),
        findings=(),
    )


def test_terminal_review_annotates_provider_model_transport() -> None:
    """Terminal output shows provider, model, transport, and max cost with sources."""
    console = Console(record=True)
    render_review_terminal(result=_review_result_with_sources(), console=console)
    text = console.export_text()

    assert_that(text).contains("Model: cursor-grok-4.6-high (flag)")
    assert_that(text).contains("Provider: cursor (env)")
    assert_that(text).contains("Transport: cli (config)")
    assert_that(text).contains("Max cost: uncapped (env)")


def test_pr_comment_mechanics_annotates_sources() -> None:
    """Posted PR comment mechanics name the source of each override field."""
    mechanics = format_run_mechanics(metadata=_review_result_with_sources().metadata)

    assert_that(mechanics).contains("**Model:** `cursor-grok-4.6-high` (flag)")
    assert_that(mechanics).contains("**Provider:** `cursor` (env)")
    assert_that(mechanics).contains("**Transport:** `cli` (config)")
    assert_that(mechanics).contains("**Max cost:** uncapped (env)")
