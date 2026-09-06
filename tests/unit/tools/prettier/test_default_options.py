"""Tests for PrettierPlugin default options."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from assertpy import assert_that

from lintro.tools.prettier.definition import PRETTIER_DEFAULT_TIMEOUT

if TYPE_CHECKING:
    from lintro.tools.prettier.definition import PrettierPlugin


def test_default_options_timeout(prettier_plugin: PrettierPlugin) -> None:
    """Default options include timeout.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
    """
    assert_that(prettier_plugin.definition.default_options["timeout"]).is_equal_to(
        PRETTIER_DEFAULT_TIMEOUT,
    )


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("verbose_fix_output", False),
        ("line_length", None),
    ],
    ids=[
        "verbose_fix_output_is_false",
        "line_length_is_none",
    ],
)
def test_default_options_values(
    prettier_plugin: PrettierPlugin,
    option_name: str,
    expected_value: Any,
) -> None:
    """Default options have correct values.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(
        prettier_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)
