"""Tests for OxfmtPlugin.set_options() method."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.tools.oxfmt.definition import OxfmtPlugin


# =============================================================================
# Tests for config and ignore_path options validation
# =============================================================================


def test_config_accepts_string(oxfmt_plugin: OxfmtPlugin) -> None:
    """Config option accepts a string value.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    oxfmt_plugin.set_options(config=".oxfmtrc.custom.json")
    assert_that(oxfmt_plugin.options.get("config")).is_equal_to(
        ".oxfmtrc.custom.json",
    )


def test_config_rejects_non_string(oxfmt_plugin: OxfmtPlugin) -> None:
    """Config option rejects non-string values.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    with pytest.raises(ValueError, match="config must be a string"):
        # Intentionally passing wrong type to test validation
        oxfmt_plugin.set_options(config=123)  # type: ignore[arg-type]


def test_ignore_path_accepts_string(oxfmt_plugin: OxfmtPlugin) -> None:
    """Ignore_path option accepts a string value.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    oxfmt_plugin.set_options(ignore_path=".oxfmtignore")
    assert_that(oxfmt_plugin.options.get("ignore_path")).is_equal_to(
        ".oxfmtignore",
    )


def test_ignore_path_rejects_non_string(oxfmt_plugin: OxfmtPlugin) -> None:
    """Ignore_path option rejects non-string values.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    with pytest.raises(ValueError, match="ignore_path must be a string"):
        # Intentionally passing wrong type to test validation
        oxfmt_plugin.set_options(ignore_path=True)  # type: ignore[arg-type]


# =============================================================================
# Tests for setting multiple options
# =============================================================================


def test_set_multiple_options(oxfmt_plugin: OxfmtPlugin) -> None:
    """Multiple options can be set in a single call.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    oxfmt_plugin.set_options(
        config=".oxfmtrc.json",
        ignore_path=".oxfmtignore",
    )

    assert_that(oxfmt_plugin.options.get("config")).is_equal_to(".oxfmtrc.json")
    assert_that(oxfmt_plugin.options.get("ignore_path")).is_equal_to(".oxfmtignore")


# =============================================================================
# Tests for _build_oxfmt_args(oxfmt_plugin.options) helper method
# =============================================================================


def test_build_args_empty_options(oxfmt_plugin: OxfmtPlugin) -> None:
    """Empty options returns empty args list.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    args = oxfmt_plugin._build_oxfmt_args(oxfmt_plugin.options)
    assert_that(args).is_empty()


def test_build_args_config_adds_flag(oxfmt_plugin: OxfmtPlugin) -> None:
    """Config option adds --config flag.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    oxfmt_plugin.set_options(config=".oxfmtrc.json")
    args = oxfmt_plugin._build_oxfmt_args(oxfmt_plugin.options)
    assert_that(args).contains("--config", ".oxfmtrc.json")


def test_build_args_ignore_path_adds_flag(oxfmt_plugin: OxfmtPlugin) -> None:
    """Ignore_path option adds --ignore-path flag.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    oxfmt_plugin.set_options(ignore_path=".oxfmtignore")
    args = oxfmt_plugin._build_oxfmt_args(oxfmt_plugin.options)
    assert_that(args).contains("--ignore-path", ".oxfmtignore")


def test_build_args_multiple_options_combine(oxfmt_plugin: OxfmtPlugin) -> None:
    """Multiple options combine into a single args list.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    oxfmt_plugin.set_options(
        config=".oxfmtrc.json",
        ignore_path=".oxfmtignore",
    )
    args = oxfmt_plugin._build_oxfmt_args(oxfmt_plugin.options)

    assert_that(args).contains("--config", ".oxfmtrc.json")
    assert_that(args).contains("--ignore-path", ".oxfmtignore")
