"""Tests for AI transport configuration validation."""

from __future__ import annotations

import pytest
from assertpy import assert_that
from pydantic import ValidationError

from lintro.ai.config import AIConfig
from lintro.ai.doctor_checks import check_ai_configuration
from lintro.ai.enums import AITransport
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


def test_cursor_api_combo_rejected() -> None:
    """Cursor + api is an invalid provider/transport combination."""
    with pytest.raises(ValidationError, match="cursor provider only supports"):
        AIConfig(
            enabled=True,
            provider="cursor",  # type: ignore[arg-type]
            transport=AITransport.API,
        )


@pytest.mark.parametrize(
    ("provider", "transport"),
    [
        ("anthropic", AITransport.API),
        ("anthropic", AITransport.CLI),
        ("openai", AITransport.API),
        ("openai", AITransport.CLI),
        ("cursor", AITransport.CLI),
    ],
)
def test_valid_provider_transport_matrix(
    provider: str,
    transport: AITransport,
) -> None:
    """Valid provider and transport pairs are accepted."""
    config = AIConfig(
        enabled=True,
        provider=provider,  # type: ignore[arg-type]
        transport=transport,
    )
    assert_that(config.transport).is_equal_to(transport)


def test_transport_yaml_string_coercion() -> None:
    """Transport accepts hyphenated string values from YAML."""
    config = AIConfig(
        enabled=True,
        transport="cli",  # type: ignore[arg-type]
    )
    assert_that(config.transport).is_equal_to(AITransport.CLI)
