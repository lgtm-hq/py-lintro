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
    assert_that(configs).contains(".spectral.yaml")
    assert_that(configs).contains(".spectral.json")


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


def test_doc_url_returns_reference(spectral_plugin: SpectralPlugin) -> None:
    """doc_url returns the spectral rules reference for any code.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    url = spectral_plugin.doc_url("oas3-api-servers")
    assert_that(url).contains("stoplight.io")


def test_fix_raises_not_implemented(spectral_plugin: SpectralPlugin) -> None:
    """Fix raises NotImplementedError since spectral cannot fix.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    with pytest.raises(NotImplementedError):
        spectral_plugin.fix(["openapi.yaml"], {})
