"""Tests for the SpectralPlugin definition and options."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.tools.core.tool_registry import ManifestRegistry
from lintro.tools.core.version_parsing import (
    TOOLS_WITH_SIMPLE_VERSION_PATTERN,
    extract_version_from_output,
)
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
    "code",
    [
        "oas2-schema",
        "oas3-api-servers",
        "oas3_1-servers-in-webhook",
        "openapi-tags",
        "operation-operationId",
        "info-contact",
        "asyncapi-info-contact",
        "asyncapi-3-tags",
        "arazzo-workflow-id",
    ],
)
def test_doc_url_routes_known_builtins_to_live_ruleset_docs(
    spectral_plugin: SpectralPlugin,
    code: str,
) -> None:
    """Known built-ins link to the live official Spectral rulesets guide.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        code: Spectral rule code.
    """
    assert_that(spectral_plugin.doc_url(code)).is_equal_to(
        str(DocUrlTemplate.SPECTRAL),
    )


@pytest.mark.parametrize(
    "code",
    [
        "",
        "my-org-rule",
        "no-unused-path",
        "info-company-extension",
        "schema-required",
    ],
)
def test_doc_url_skips_custom_and_json_schema_codes(
    spectral_plugin: SpectralPlugin,
    code: str,
) -> None:
    """Custom / JSON Schema codes must not inherit the OpenAPI rules page.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        code: Rule code without a known built-in documentation mapping.
    """
    assert_that(spectral_plugin.doc_url(code)).is_none()


def test_spectral_version_uses_simple_numeric_parser() -> None:
    """Spectral's numeric ``--version`` output uses the shared parser."""
    expected = get_min_version(ToolName.SPECTRAL)

    assert_that(expected).is_not_none()
    assert_that(ToolName.SPECTRAL in TOOLS_WITH_SIMPLE_VERSION_PATTERN).is_true()
    assert_that(
        extract_version_from_output(f"spectral {expected}", "spectral"),
    ).is_equal_to(
        expected,
    )


def test_spectral_is_config_selected_not_language_mapped(
    spectral_plugin: SpectralPlugin,
) -> None:
    """Recommended language detection must not enable Spectral for all YAML."""
    registry = ManifestRegistry.load()
    mapped = {name for tools in registry.language_map.values() for name in tools}
    native_configs = spectral_plugin.definition.native_configs

    assert_that(mapped).does_not_contain("spectral")
    assert_that(native_configs).contains(
        ".spectral.yaml",
    )
    assert_that(native_configs).contains(".spectral.yml")


def test_installer_wires_spectral_through_every_sync_point() -> None:
    """Installer help, filtering, installation, and verification stay aligned."""
    script = Path("scripts/utils/install-tools.sh").read_text(encoding="utf-8")

    assert_that(script).contains(
        "- Spectral (OpenAPI/AsyncAPI/JSON Schema linter)",
        '"spectral" "sqlfluff"',
        'if should_install "spectral"; then',
        'get_tool_version "@stoplight/spectral-cli"',
        '["spectral"]="OpenAPI/AsyncAPI/JSON Schema linting"',
    )
    assert_that(script).contains(
        '"shfmt" "spectral" "sqlfluff"',
    )


def test_fix_raises_not_implemented(spectral_plugin: SpectralPlugin) -> None:
    """Fix raises NotImplementedError since spectral cannot fix.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    with pytest.raises(NotImplementedError):
        spectral_plugin.fix(["openapi.yaml"], {})
