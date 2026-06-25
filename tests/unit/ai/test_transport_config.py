"""Tests for AI transport configuration validation."""

from __future__ import annotations

import pytest
from assertpy import assert_that
from pydantic import ValidationError

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport


def test_transport_optional_when_ai_disabled() -> None:
    """Disabled AI config does not require transport."""
    config = AIConfig(enabled=False)
    assert_that(config.transport).is_none()


def test_transport_required_when_ai_enabled() -> None:
    """Enabled AI config requires explicit transport."""
    with pytest.raises(ValidationError, match="ai.transport is required"):
        AIConfig(enabled=True)


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
