"""Unit tests for yamllint plugin options."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.tools.definitions.yamllint import YamllintPlugin


def test_default_options(yamllint_plugin: YamllintPlugin) -> None:
    """Default options include expected keys.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
    """
    defaults = yamllint_plugin.definition.default_options
    assert_that(defaults).contains_key("format")
    assert_that(defaults["format"]).is_equal_to("parsable")
    assert_that(defaults["strict"]).is_false()


def test_set_options_strict(yamllint_plugin: YamllintPlugin) -> None:
    """Set strict option.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
    """
    yamllint_plugin.set_options(strict=True)
    assert_that(yamllint_plugin.options.get("strict")).is_true()


def test_set_options_format_normalizes(yamllint_plugin: YamllintPlugin) -> None:
    """Set format option normalizes the value.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
    """
    yamllint_plugin.set_options(format="github")
    assert_that(yamllint_plugin.options.get("format")).is_equal_to("github")


def test_set_options_invalid_config_file_type(
    yamllint_plugin: YamllintPlugin,
) -> None:
    """Raise ValueError for invalid config_file type.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
    """
    with pytest.raises(ValueError, match="config_file must be"):
        yamllint_plugin.set_options(config_file=123)  # type: ignore[arg-type]


def test_should_ignore_file_matches_prefix(yamllint_plugin: YamllintPlugin) -> None:
    """A file whose path starts with an ignore pattern is ignored.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
    """
    result = yamllint_plugin._should_ignore_file(
        "vendor/config.yaml",
        ["vendor/"],
    )
    assert_that(result).is_true()


def test_should_ignore_file_no_match(yamllint_plugin: YamllintPlugin) -> None:
    """A file that doesn't match any ignore pattern is not ignored.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
    """
    result = yamllint_plugin._should_ignore_file(
        "src/config.yaml",
        ["vendor/"],
    )
    assert_that(result).is_false()
