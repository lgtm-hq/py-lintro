"""Tests for pydoclint plugin default options."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.tools.pydoclint.definition import (
    PYDOCLINT_DEFAULT_TIMEOUT,
    PydoclintPlugin,
)


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", PYDOCLINT_DEFAULT_TIMEOUT),
        ("quiet", True),
    ],
    ids=[
        "timeout_equals_default",
        "quiet_is_true",
    ],
)
def test_default_options_values(
    pydoclint_plugin: PydoclintPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have correct values.

    Args:
        pydoclint_plugin: The PydoclintPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(
        pydoclint_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)
