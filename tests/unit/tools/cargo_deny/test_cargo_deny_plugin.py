"""Unit tests for cargo-deny plugin."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.cargo_deny import CargoDenyPlugin


@pytest.fixture
def cargo_deny_plugin() -> CargoDenyPlugin:
    """Provide a CargoDenyPlugin instance for testing.

    Returns:
        A CargoDenyPlugin instance.
    """
    return CargoDenyPlugin()


def test_definition_name(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify the tool name.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    assert_that(cargo_deny_plugin.definition.name).is_equal_to("cargo_deny")


def test_definition_can_fix(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify the tool cannot fix issues.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    assert_that(cargo_deny_plugin.definition.can_fix).is_false()


def test_definition_tool_type(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify the tool type is SECURITY | INFRASTRUCTURE.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    expected_type = ToolType.SECURITY | ToolType.INFRASTRUCTURE
    assert_that(cargo_deny_plugin.definition.tool_type).is_equal_to(expected_type)


def test_definition_file_patterns(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify the file patterns.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    patterns = cargo_deny_plugin.definition.file_patterns
    assert_that(patterns).contains("Cargo.toml")
    assert_that(patterns).contains("deny.toml")


def test_definition_priority(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify the priority is 90.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    assert_that(cargo_deny_plugin.definition.priority).is_equal_to(90)


def test_definition_timeout(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify the default timeout is 60.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    assert_that(cargo_deny_plugin.definition.default_timeout).is_equal_to(60)


def test_definition_native_configs(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify the native config files.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    assert_that(cargo_deny_plugin.definition.native_configs).contains("deny.toml")


def test_fix_raises_not_implemented(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify fix raises NotImplementedError.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    with pytest.raises(NotImplementedError) as exc_info:
        cargo_deny_plugin.fix(["."], {})
    assert_that(str(exc_info.value)).contains("cannot automatically fix")


def test_set_options_timeout(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify timeout option can be set.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    cargo_deny_plugin.set_options(timeout=120)
    assert_that(cargo_deny_plugin.options.get("timeout")).is_equal_to(120)


def test_set_options_invalid_timeout(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify invalid timeout raises ValueError.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    with pytest.raises(ValueError):
        cargo_deny_plugin.set_options(timeout=-1)
