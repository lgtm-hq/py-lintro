"""Tests for the SpectralPlugin definition and options."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.spectral import SpectralPlugin


def test_definition_name(spectral_plugin: SpectralPlugin) -> None:
    """The plugin exposes the expected tool name.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    assert_that(spectral_plugin.definition.name).is_equal_to("spectral")


def test_definition_is_check_only(spectral_plugin: SpectralPlugin) -> None:
    """Spectral is a linter with no fixer.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    assert_that(spectral_plugin.definition.can_fix).is_false()
    assert_that(spectral_plugin.definition.tool_type).is_equal_to(ToolType.LINTER)


def test_definition_file_patterns(spectral_plugin: SpectralPlugin) -> None:
    """The plugin targets YAML/JSON API documents.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    patterns = spectral_plugin.definition.file_patterns
    assert_that(patterns).contains("*.yaml")
    assert_that(patterns).contains("*.yml")
    assert_that(patterns).contains("*.json")


def test_definition_native_configs(spectral_plugin: SpectralPlugin) -> None:
    """The plugin advertises spectral ruleset filenames.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    configs = spectral_plugin.definition.native_configs
    assert_that(configs).contains(
        ".spectral.yaml",
        ".spectral.yml",
        ".spectral.json",
        ".spectral.js",
    )


def test_definition_version_command(spectral_plugin: SpectralPlugin) -> None:
    """The plugin uses ``spectral --version`` for version checks.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    assert_that(spectral_plugin.definition.version_command).is_equal_to(
        ["spectral", "--version"],
    )


def test_set_options_timeout_and_ruleset(spectral_plugin: SpectralPlugin) -> None:
    """set_options stores timeout and ruleset overrides.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    spectral_plugin.set_options(timeout=60, ruleset="custom.spectral.yaml")
    assert_that(spectral_plugin.options.get("timeout")).is_equal_to(60)
    assert_that(spectral_plugin.options.get("ruleset")).is_equal_to(
        "custom.spectral.yaml",
    )


def test_set_options_rejects_invalid_timeout(spectral_plugin: SpectralPlugin) -> None:
    """set_options rejects a non-positive timeout.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    with pytest.raises(ValueError):
        spectral_plugin.set_options(timeout=0)


@pytest.mark.parametrize(
    ("code", "page_fragment", "anchor"),
    [
        ("oas3-api-servers", "openapi-rules.md", "#oas3-api-servers"),
        ("operation-operationId", "openapi-rules.md", "#operation-operationId"),
        ("asyncapi-info-contact", "asyncapi-rules.md", "#asyncapi-info-contact"),
        ("asyncapi-3-tags", "asyncapi-rules.md", "#asyncapi-3-tags"),
        ("arazzo-workflow-id", "arazzo-rules.md", "#arazzo-workflow-id"),
    ],
)
def test_doc_url_routes_by_ruleset_prefix(
    spectral_plugin: SpectralPlugin,
    code: str,
    page_fragment: str,
    anchor: str,
) -> None:
    """doc_url picks the OpenAPI, AsyncAPI, or Arazzo rules page by prefix.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        code: Spectral rule code.
        page_fragment: Filename of the ruleset docs page.
        anchor: Expected URL fragment for the rule.
    """
    url = spectral_plugin.doc_url(code)
    assert_that(url).contains("meta.stoplight.io")
    assert_that(url).contains(page_fragment)
    assert_that(url).contains(anchor)


def test_doc_url_skips_custom_and_empty_codes(
    spectral_plugin: SpectralPlugin,
) -> None:
    """Custom / JSON Schema codes must not inherit the OpenAPI rules page.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    assert_that(spectral_plugin.doc_url("my-org-rule")).is_none()
    assert_that(spectral_plugin.doc_url("")).is_none()


def test_fix_raises_not_implemented(spectral_plugin: SpectralPlugin) -> None:
    """Fix raises NotImplementedError since spectral cannot fix.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    with pytest.raises(NotImplementedError):
        spectral_plugin.fix(["openapi.yaml"], {})
