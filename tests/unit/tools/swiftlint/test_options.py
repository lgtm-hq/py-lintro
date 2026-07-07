"""Unit tests for SwiftlintPlugin options and definition defaults."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.tools.definitions.swiftlint import SwiftlintPlugin


def test_default_options_include_timeout(
    swiftlint_plugin: SwiftlintPlugin,
) -> None:
    """The definition ships a default timeout.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
    """
    assert_that(swiftlint_plugin.definition.default_options).contains_key("timeout")
    assert_that(swiftlint_plugin.definition.default_timeout).is_equal_to(60)


def test_set_options_accepts_timeout(
    swiftlint_plugin: SwiftlintPlugin,
) -> None:
    """A valid positive timeout is stored on the plugin options.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
    """
    swiftlint_plugin.set_options(timeout=120)
    assert_that(swiftlint_plugin.options.get("timeout")).is_equal_to(120)


def test_set_options_rejects_non_positive_timeout(
    swiftlint_plugin: SwiftlintPlugin,
) -> None:
    """A non-positive timeout is rejected.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
    """
    with pytest.raises(ValueError):
        swiftlint_plugin.set_options(timeout=0)


def test_set_options_none_timeout_keeps_default(
    swiftlint_plugin: SwiftlintPlugin,
) -> None:
    """Passing ``timeout=None`` leaves the seeded default (60) in place.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
    """
    swiftlint_plugin.set_options(timeout=None)
    # filter_none_options drops None, so the default option is untouched.
    assert_that(swiftlint_plugin.options.get("timeout")).is_equal_to(60)
