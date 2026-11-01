"""Tests for transport-scoped AI config resolution (#1923)."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig, AITransportProfiles, CliTransportProfile
from lintro.ai.enums import AITransport, CostBasis
from lintro.ai.providers.anthropic.metadata import ANTHROPIC_MANAGED_SETTINGS_ENV
from lintro.ai.registry import AIProvider
from lintro.ai.transport import (
    DEFAULT_API_TIMEOUT,
    DEFAULT_CLI_TIMEOUT,
    apply_resolved_transport,
    format_resolved_profile_log,
    resolve_transport_settings,
)


@pytest.fixture(autouse=True)
def _isolate_bare_mode_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Clear ambient bare-mode and provenance inputs so CLI provenance is fixed.

    ``resolve_transport_settings`` consults ``should_send_bare`` on the CLI
    branch and, since #2867, the credential and base-URL override the run
    would use. A developer machine exporting ``ANTHROPIC_API_KEY`` would flip
    ``subscription``/``unpriceable`` to ``api_key``/``estimated``, and a Claude
    settings file routing through a proxy would flip the basis to ``unknown``.
    Tests that need those inputs set them explicitly via monkeypatch.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Per-test directory the Claude settings lookups stay inside.
    """
    for name in (
        "ANTHROPIC_API_KEY",
        "LINTRO_CLI_BARE",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "LINTRO_REVIEW_LANE",
        "LINTRO_REVIEW_ENDPOINT",
        "LINTRO_REVIEW_FIRST_PARTY",
    ):
        monkeypatch.delenv(name, raising=False)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv(
        ANTHROPIC_MANAGED_SETTINGS_ENV,
        str(tmp_path / "managed-settings.json"),
    )


def test_cli_defaults_to_per_chunk_timeout() -> None:
    """CLI without a profile uses the 1800s per-chunk default."""
    config = AIConfig(enabled=True, transport=AITransport.CLI)
    resolved = resolve_transport_settings(config)
    assert_that(resolved.timeout).is_equal_to(DEFAULT_CLI_TIMEOUT)
    assert_that(resolved.cost_is_advisory).is_true()
    assert_that(resolved.auth_mode).is_equal_to("subscription")
    assert_that(resolved.cost_basis).is_equal_to(CostBasis.UNPRICEABLE)


def test_api_defaults_to_stream_timeout() -> None:
    """API without a profile uses the 60s stream-sized default."""
    config = AIConfig(enabled=True, transport=AITransport.API)
    resolved = resolve_transport_settings(config)
    assert_that(resolved.timeout).is_equal_to(DEFAULT_API_TIMEOUT)
    assert_that(resolved.cost_is_advisory).is_false()
    assert_that(resolved.auth_mode).is_equal_to("api_key")
    assert_that(resolved.cost_basis).is_equal_to(CostBasis.BILLED)


def test_cli_profile_timeout_and_advisory_cost_win() -> None:
    """CLI transport profile overrides built-in defaults."""
    config = AIConfig(
        enabled=True,
        transport=AITransport.CLI,
        max_cost_usd=9.99,
        transports=AITransportProfiles(
            cli=CliTransportProfile(timeout=1200, max_cost_usd_advisory=0.5),
        ),
    )
    resolved = resolve_transport_settings(config)
    assert_that(resolved.timeout).is_equal_to(1200)
    assert_that(resolved.max_cost_usd).is_equal_to(0.5)
    assert_that(resolved.cost_is_advisory).is_true()


def test_cli_legacy_max_cost_is_advisory_fallback() -> None:
    """Legacy max_cost_usd fills the CLI advisory when the profile omits it."""
    config = AIConfig(
        enabled=True,
        transport=AITransport.CLI,
        max_cost_usd=0.25,
    )
    resolved = resolve_transport_settings(config)
    assert_that(resolved.max_cost_usd).is_equal_to(0.25)


def test_apply_resolved_transport_writes_legacy_scalars() -> None:
    """Resolved values are copied onto api_timeout/max_cost_usd for callers."""
    config = AIConfig(
        enabled=True,
        transport=AITransport.CLI,
        transports=AITransportProfiles(
            cli=CliTransportProfile(timeout=900, max_cost_usd_advisory=0.5),
        ),
    )
    applied = apply_resolved_transport(config)
    assert_that(applied.api_timeout).is_equal_to(900)
    assert_that(applied.max_cost_usd).is_equal_to(0.5)


def test_api_profile_timeout_beats_legacy_scalar() -> None:
    """API transport profile timeout wins over ai.api_timeout."""
    from lintro.ai.config import ApiTransportProfile

    config = AIConfig(
        enabled=True,
        transport=AITransport.API,
        api_timeout=120.0,
        transports=AITransportProfiles(
            api=ApiTransportProfile(timeout=45.0),
        ),
    )
    resolved = resolve_transport_settings(config)
    assert_that(resolved.timeout).is_equal_to(45.0)


def test_flag_timeout_via_profile_mutation_wins() -> None:
    """Writing the active profile timeout (as --timeout does) is honored."""
    config = AIConfig(
        enabled=True,
        transport=AITransport.CLI,
        transports=AITransportProfiles(
            cli=CliTransportProfile(timeout=900),
        ),
    )
    transports = config.transports.model_copy(deep=True)
    transports.cli.timeout = 42.0
    config = config.model_copy(update={"transports": transports})
    resolved = resolve_transport_settings(config)
    assert_that(resolved.timeout).is_equal_to(42.0)


def test_format_resolved_profile_log_names_advisory_cap() -> None:
    """Log line is self-describing for CI."""
    config = AIConfig(
        enabled=True,
        transport=AITransport.CLI,
        transports=AITransportProfiles(
            cli=CliTransportProfile(timeout=900, max_cost_usd_advisory=0.5),
        ),
    )
    line = format_resolved_profile_log(resolve_transport_settings(config))
    assert_that(line).contains("transport=cli")
    assert_that(line).contains("auth=subscription")
    assert_that(line).contains("timeout=900")
    assert_that(line).contains("advisory:$0.50")
    assert_that(line).contains("cost_basis=unpriceable")


def test_cli_auth_mode_reports_api_key_when_bare_billing_engages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI transport with a reachable Anthropic key bills the key, not the sub.

    CliBareMode.AUTO sends --bare when ANTHROPIC_API_KEY is reachable (#1859),
    so cost provenance must say api_key/estimated instead of
    subscription/unpriceable (#1923).
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    config = AIConfig(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.CLI,
    )

    resolved = resolve_transport_settings(config)

    assert_that(resolved.auth_mode).is_equal_to("api_key")
    assert_that(resolved.cost_basis).is_equal_to(CostBasis.ESTIMATED)


def test_cli_auth_mode_reports_subscription_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI transport without a reachable key stays subscription/unpriceable."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LINTRO_CLI_BARE", raising=False)
    config = AIConfig(transport=AITransport.CLI)

    resolved = resolve_transport_settings(config)

    assert_that(resolved.auth_mode).is_equal_to("subscription")
    assert_that(resolved.cost_basis).is_equal_to(CostBasis.UNPRICEABLE)
