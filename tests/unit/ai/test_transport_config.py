"""Tests for AI transport configuration validation."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.doctor_checks import check_ai_configuration
from lintro.ai.enums import AITransport
from lintro.ai.registry import AIProvider
from lintro.enums.tool_status import ToolStatus


def test_transport_optional_when_ai_disabled() -> None:
    """Disabled AI config does not require transport."""
    config = AIConfig(enabled=False)
    assert_that(config.transport).is_none()


def test_transport_deferred_to_doctor_when_ai_enabled() -> None:
    """Enabled AI config may omit transport until doctor validates it."""
    config = AIConfig(enabled=True)
    assert_that(config.transport).is_none()


def test_doctor_reports_missing_transport_when_ai_enabled() -> None:
    """Doctor surfaces missing transport instead of raw config validation."""
    results = check_ai_configuration(AIConfig(enabled=True))
    assert_that(results).is_length(1)
    assert_that(results[0].name).is_equal_to("ai.transport")
    assert_that(results[0].status).is_equal_to(ToolStatus.INCOMPATIBLE)


def test_doctor_reports_missing_transport_when_only_lint_enabled() -> None:
    """Doctor validates transport when only ai.lint is enabled."""
    results = check_ai_configuration(AIConfig(enabled=True, lint=True, review=False))
    assert_that(results).is_length(1)
    assert_that(results[0].name).is_equal_to("ai.transport")
    assert_that(results[0].message).contains("ai.lint or ai.review")


def test_doctor_reports_missing_transport_when_only_review_enabled() -> None:
    """Doctor validates transport when only ai.review is enabled."""
    results = check_ai_configuration(AIConfig(enabled=True, lint=False, review=True))
    assert_that(results).is_length(1)
    assert_that(results[0].name).is_equal_to("ai.transport")


def test_doctor_skips_when_master_switch_off() -> None:
    """Sub-toggles alone (master off) produce no doctor checks."""
    results = check_ai_configuration(AIConfig(enabled=False, lint=True, review=True))
    assert_that(results).is_empty()


def test_doctor_reports_cursor_api_combo_when_enabled() -> None:
    """Doctor surfaces invalid cursor+api instead of raw config validation."""
    results = check_ai_configuration(
        AIConfig(
            enabled=True,
            provider=AIProvider.CURSOR,
            transport=AITransport.API,
        ),
    )
    assert_that(results).is_length(1)
    assert_that(results[0].name).is_equal_to("ai.provider+transport")
    assert_that(results[0].status).is_equal_to(ToolStatus.INCOMPATIBLE)


def test_cursor_api_combo_allowed_when_disabled() -> None:
    """Disabled configs may keep stale cursor/api settings for doctor."""
    config = AIConfig(
        enabled=False,
        provider=AIProvider.CURSOR,
        transport=AITransport.API,
    )
    assert_that(config.transport).is_equal_to(AITransport.API)


@pytest.mark.parametrize(
    ("provider", "transport"),
    [
        (AIProvider.ANTHROPIC, AITransport.API),
        (AIProvider.ANTHROPIC, AITransport.CLI),
        (AIProvider.OPENAI, AITransport.API),
        (AIProvider.OPENAI, AITransport.CLI),
        (AIProvider.CURSOR, AITransport.CLI),
    ],
)
def test_valid_provider_transport_matrix(
    provider: AIProvider,
    transport: AITransport,
) -> None:
    """Valid provider and transport pairs are accepted."""
    config = AIConfig(
        enabled=True,
        provider=provider,
        transport=transport,
    )
    assert_that(config.transport).is_equal_to(transport)


def test_transport_yaml_string_coercion() -> None:
    """Transport accepts hyphenated string values from YAML."""
    config = AIConfig.model_validate({"enabled": True, "transport": "cli"})
    assert_that(config.transport).is_equal_to(AITransport.CLI)
