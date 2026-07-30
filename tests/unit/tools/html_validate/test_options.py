"""Unit tests for the html-validate plugin definition and metadata."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.html_validate import HtmlValidatePlugin


def test_definition_metadata(html_validate_plugin: HtmlValidatePlugin) -> None:
    """The definition exposes the expected metadata.

    Args:
        html_validate_plugin: The plugin under test.
    """
    definition = html_validate_plugin.definition
    assert_that(definition.name).is_equal_to("html_validate")
    assert_that(definition.can_fix).is_false()
    assert_that(definition.tool_type).is_equal_to(ToolType.LINTER)
    assert_that(definition.file_patterns).contains("*.html", "*.htm")
    assert_that(definition.version_command).is_equal_to(
        ["html-validate", "--version"],
    )
    assert_that(definition.native_configs).contains(".htmlvalidate.json")


def test_doc_url_simple_rule(html_validate_plugin: HtmlValidatePlugin) -> None:
    """A simple rule id maps to its documentation page.

    Args:
        html_validate_plugin: The plugin under test.
    """
    assert_that(html_validate_plugin.doc_url("no-implicit-close")).is_equal_to(
        "https://html-validate.org/rules/no-implicit-close.html",
    )


def test_doc_url_namespaced_rule(html_validate_plugin: HtmlValidatePlugin) -> None:
    """A namespaced rule id maps into the rule doc path.

    Args:
        html_validate_plugin: The plugin under test.
    """
    assert_that(html_validate_plugin.doc_url("wcag/h37")).is_equal_to(
        "https://html-validate.org/rules/wcag/h37.html",
    )


def test_doc_url_empty_returns_none(
    html_validate_plugin: HtmlValidatePlugin,
) -> None:
    """An empty code yields no documentation URL.

    Args:
        html_validate_plugin: The plugin under test.
    """
    assert_that(html_validate_plugin.doc_url("")).is_none()
