"""Unit tests for ValePlugin definition and options."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.vale import ValePlugin


def test_definition_metadata(vale_plugin: ValePlugin) -> None:
    """The definition should expose the expected metadata.

    Args:
        vale_plugin: The ValePlugin instance under test.
    """
    d = vale_plugin.definition

    assert_that(d.name).is_equal_to("vale")
    assert_that(d.can_fix).is_false()
    assert_that(d.file_patterns).contains("*.md", "*.rst", "*.adoc", "*.txt")
    assert_that(d.native_configs).contains(".vale.ini", "_vale.ini", "vale.ini")
    assert_that(bool(d.tool_type & ToolType.LINTER)).is_true()
    assert_that(bool(d.tool_type & ToolType.DOCUMENTATION)).is_true()


def test_build_command_default(vale_plugin: ValePlugin) -> None:
    """The base command should request JSON output.

    Args:
        vale_plugin: The ValePlugin instance under test.
    """
    cmd = vale_plugin._build_command()

    assert_that(cmd).contains("vale", "--output=JSON")


def test_build_command_with_config_and_level(vale_plugin: ValePlugin) -> None:
    """Explicit config and alert level should be added to the command.

    Args:
        vale_plugin: The ValePlugin instance under test.
    """
    vale_plugin.set_options(config="custom.ini", min_alert_level="warning")

    cmd = vale_plugin._build_command()

    assert_that(cmd).contains("--config", "custom.ini")
    assert_that(cmd).contains("--minAlertLevel", "warning")


def test_set_options_rejects_invalid_timeout(vale_plugin: ValePlugin) -> None:
    """A non-positive timeout should be rejected.

    Args:
        vale_plugin: The ValePlugin instance under test.
    """
    with pytest.raises((ValueError, TypeError)):
        vale_plugin.set_options(timeout=0)


def test_is_no_config_error_detection(vale_plugin: ValePlugin) -> None:
    """The no-config detector should recognize Vale's E100 error.

    Args:
        vale_plugin: The ValePlugin instance under test.
    """
    assert_that(vale_plugin._is_no_config_error("E100 [.vale.ini not found]")).is_true()
    assert_that(vale_plugin._is_no_config_error("no config file found")).is_true()
    assert_that(vale_plugin._is_no_config_error("{}")).is_false()
    assert_that(vale_plugin._is_no_config_error(None)).is_false()
