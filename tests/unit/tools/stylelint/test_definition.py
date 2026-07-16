"""Tests for StylelintPlugin definition, options, and doc URLs."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.stylelint import (
    STYLELINT_DEFAULT_TIMEOUT,
    StylelintPlugin,
)


def test_definition_metadata(stylelint_plugin: StylelintPlugin) -> None:
    """The tool definition exposes the expected metadata."""
    definition = stylelint_plugin.definition
    assert_that(definition.name).is_equal_to("stylelint")
    assert_that(definition.can_fix).is_true()
    assert_that(definition.file_patterns).is_equal_to(
        ["*.css", "*.scss", "*.sass", "*.less"],
    )
    assert_that(bool(definition.tool_type & ToolType.LINTER)).is_true()
    assert_that(bool(definition.tool_type & ToolType.FORMATTER)).is_true()
    assert_that(definition.version_command).is_equal_to(["stylelint", "--version"])


def test_default_options(stylelint_plugin: StylelintPlugin) -> None:
    """Default options carry the expected timeout."""
    assert_that(stylelint_plugin.options.get("timeout")).is_equal_to(
        STYLELINT_DEFAULT_TIMEOUT,
    )


def test_set_options_config_and_verbose(
    stylelint_plugin: StylelintPlugin,
) -> None:
    """set_options records config and verbose_fix_output."""
    stylelint_plugin.set_options(config="c.json", verbose_fix_output=True)
    assert_that(stylelint_plugin.options.get("config")).is_equal_to("c.json")
    assert_that(stylelint_plugin.options.get("verbose_fix_output")).is_true()


def test_set_options_rejects_bad_types(stylelint_plugin: StylelintPlugin) -> None:
    """set_options validates option types."""
    with pytest.raises((ValueError, TypeError)):
        stylelint_plugin.set_options(config=123)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        pytest.param(
            "color-hex-length",
            "https://stylelint.io/user-guide/rules/color-hex-length",
            id="real_rule",
        ),
        pytest.param("CssSyntaxError", None, id="syntax_pseudo_rule"),
        pytest.param("parseError", None, id="parse_pseudo_rule"),
        pytest.param("", None, id="empty_code"),
    ],
)
def test_doc_url(
    stylelint_plugin: StylelintPlugin,
    code: str,
    expected: str | None,
) -> None:
    """doc_url links real rules and skips pseudo-rules."""
    assert_that(stylelint_plugin.doc_url(code)).is_equal_to(expected)
