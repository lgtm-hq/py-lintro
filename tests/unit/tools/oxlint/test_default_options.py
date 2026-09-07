"""Tests for OxlintPlugin default options."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.tools.oxlint.definition import OXLINT_DEFAULT_TIMEOUT

if TYPE_CHECKING:
    from lintro.tools.oxlint.definition import OxlintPlugin


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", OXLINT_DEFAULT_TIMEOUT),
        ("quiet", False),
    ],
    ids=[
        "timeout_equals_default",
        "quiet_is_false",
    ],
)
def test_default_options_values(
    oxlint_plugin: OxlintPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have correct values.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(oxlint_plugin.definition.default_options).contains_key(option_name)
    assert_that(oxlint_plugin.definition.default_options[option_name]).is_equal_to(
        expected_value,
    )


def test_default_timeout_constant() -> None:
    """Test that OXLINT_DEFAULT_TIMEOUT has expected value."""
    assert_that(OXLINT_DEFAULT_TIMEOUT).is_equal_to(30)


def test_plugin_options_initialized(oxlint_plugin: OxlintPlugin) -> None:
    """Test that plugin options are initialized with defaults.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    assert_that(oxlint_plugin.options).contains_key("quiet")
    assert_that(oxlint_plugin.options["quiet"]).is_false()
