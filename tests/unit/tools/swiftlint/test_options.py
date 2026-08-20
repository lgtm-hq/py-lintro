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


def test_build_check_command_uses_json_reporter(
    swiftlint_plugin: SwiftlintPlugin,
) -> None:
    """Check argv includes lint, JSON reporter, and --quiet.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
    """
    cmd = swiftlint_plugin._build_check_command(file_path="Sample.swift")

    assert_that(cmd).contains("swiftlint")
    assert_that(cmd).contains("lint")
    assert_that(cmd).contains("--reporter")
    reporter_idx = cmd.index("--reporter")
    assert_that(cmd[reporter_idx + 1]).is_equal_to("json")
    assert_that(cmd).contains("--quiet")
    assert_that(cmd).contains("Sample.swift")


def test_build_fix_command_uses_fix_and_quiet(
    swiftlint_plugin: SwiftlintPlugin,
) -> None:
    """Fix argv includes --fix and --quiet.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
    """
    cmd = swiftlint_plugin._build_fix_command(file_path="Sample.swift")

    assert_that(cmd).contains("swiftlint")
    assert_that(cmd).contains("--fix")
    assert_that(cmd).contains("--quiet")
    assert_that(cmd).contains("Sample.swift")
    assert_that(cmd).does_not_contain("lint")
