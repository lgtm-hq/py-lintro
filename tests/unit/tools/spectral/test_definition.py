"""Tests for the SpectralPlugin definition and options."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.tools.core.tool_registry import ManifestRegistry
from lintro.tools.core.version_parsing import (
    TOOLS_WITH_SIMPLE_VERSION_PATTERN,
    extract_version_from_output,
)
from lintro.tools.spectral.definition import SpectralPlugin


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
    assert_that(spectral_plugin.definition.file_patterns).is_equal_to(
        ["*.yaml", "*.yml", "*.json"],
    )


def test_definition_native_configs(spectral_plugin: SpectralPlugin) -> None:
    """The plugin advertises spectral ruleset filenames.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    assert_that(spectral_plugin.definition.native_configs).is_equal_to(
        [
            ".spectral.yaml",
            ".spectral.yml",
            ".spectral.json",
            ".spectral.js",
        ],
    )
    assert_that(spectral_plugin.definition.default_timeout).is_equal_to(30)


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
    ("code", "rules_page"),
    [
        ("oas2-schema", "openapi-rules.md"),
        ("oas3-api-servers", "openapi-rules.md"),
        ("oas3_1-servers-in-webhook", "openapi-rules.md"),
        ("openapi-tags", "openapi-rules.md"),
        ("operation-operationId", "openapi-rules.md"),
        ("info-contact", "openapi-rules.md"),
        ("path-params", "openapi-rules.md"),
        ("asyncapi-info-contact", "asyncapi-rules.md"),
        ("asyncapi-3-tags", "asyncapi-rules.md"),
        ("arazzo-workflow-id", "arazzo-rules.md"),
    ],
)
def test_doc_url_routes_known_builtins_to_official_rule_files(
    spectral_plugin: SpectralPlugin,
    code: str,
    rules_page: str,
) -> None:
    """Known built-ins link to maintained official rule files.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        code: Spectral rule code.
        rules_page: Expected rule-reference Markdown filename.
    """
    url = spectral_plugin.doc_url(code)
    assert_that(url).starts_with(
        "https://github.com/stoplightio/spectral/blob/develop/docs/reference/",
    )
    assert_that(url).contains(f"{rules_page}#{code.lower()}")


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
        extract_version_from_output(expected, "spectral"),
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
    verify_line = next(
        line
        for line in script.splitlines()
        if line.strip().startswith("tools_to_verify=")
    )
    dockerfile = Path("docker/tools.Dockerfile").read_text(encoding="utf-8")

    assert_that(script).contains(
        "- Spectral (OpenAPI/AsyncAPI/JSON Schema linter)",
        '"spectral" "sqlfluff"',
        'if should_install "spectral"; then',
        'get_tool_version "@stoplight/spectral-cli"',
        '["spectral"]="OpenAPI/AsyncAPI/JSON Schema linting"',
    )
    assert_that(verify_line).contains('"spectral"')
    assert_that(dockerfile).contains("spectral --version")


def test_repository_dogfoods_spectral_without_linting_violation_samples() -> None:
    """Root ruleset enables dogfood while intentional samples remain excluded."""
    ruleset = Path(".spectral.yaml").read_text(encoding="utf-8")
    ignore = Path(".lintro-ignore").read_text(encoding="utf-8")

    assert_that(ruleset).contains("spectral:oas")
    assert_that(ignore).contains("test_samples/")


def test_fix_raises_not_implemented(spectral_plugin: SpectralPlugin) -> None:
    """Fix raises NotImplementedError since spectral cannot fix.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
    """
    with pytest.raises(NotImplementedError):
        spectral_plugin.fix(["openapi.yaml"], {})
