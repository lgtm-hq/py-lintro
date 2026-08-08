"""Tests for transport-scoped AI config resolution (#1923)."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.config import AIConfig, AITransportProfiles, CliTransportProfile
from lintro.ai.enums import AITransport, CostBasis
from lintro.ai.transport import (
    DEFAULT_API_TIMEOUT,
    DEFAULT_CLI_TIMEOUT,
    apply_resolved_transport,
    format_resolved_profile_log,
    resolve_transport_settings,
)


def test_cli_defaults_to_whole_turn_timeout() -> None:
    """CLI without a profile uses the 900s whole-turn default."""
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
