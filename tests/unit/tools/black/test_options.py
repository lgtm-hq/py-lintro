"""Unit tests for black plugin options."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.black import BlackPlugin


def test_default_options(black_plugin: BlackPlugin) -> None:
    """Default options include expected keys.

    Args:
        black_plugin: The BlackPlugin instance to test.
    """
    defaults = black_plugin.definition.default_options
    assert_that(defaults).contains_key("line_length")
    assert_that(defaults).contains_key("fast")
    assert_that(defaults["fast"]).is_false()
    assert_that(defaults["preview"]).is_false()


def test_set_options_line_length(black_plugin: BlackPlugin) -> None:
    """Set line_length option.

    Args:
        black_plugin: The BlackPlugin instance to test.
    """
    black_plugin.set_options(line_length=100)
    assert_that(black_plugin.options.get("line_length")).is_equal_to(100)


def test_set_options_invalid_line_length_type(black_plugin: BlackPlugin) -> None:
    """Raise ValueError for invalid line_length type.

    Args:
        black_plugin: The BlackPlugin instance to test.
    """
    with pytest.raises(ValueError, match="line_length must be"):
        black_plugin.set_options(line_length="wide")


def test_set_options_fast(black_plugin: BlackPlugin) -> None:
    """Set fast option.

    Args:
        black_plugin: The BlackPlugin instance to test.
    """
    black_plugin.set_options(fast=True)
    assert_that(black_plugin.options.get("fast")).is_true()


def test_build_common_args_includes_line_length(black_plugin: BlackPlugin) -> None:
    """Build args includes --line-length when Lintro config injection is off.

    Args:
        black_plugin: The BlackPlugin instance to test.
    """
    black_plugin.set_options(line_length=100)
    with patch.object(black_plugin, "_build_config_args", return_value=[]):
        args = black_plugin._build_common_args()
    assert_that(args).contains("--line-length", "100")


def test_build_common_args_includes_fast(black_plugin: BlackPlugin) -> None:
    """Build args includes --fast when enabled.

    Args:
        black_plugin: The BlackPlugin instance to test.
    """
    black_plugin.set_options(fast=True)
    args = black_plugin._build_common_args()
    assert_that(args).contains("--fast")
