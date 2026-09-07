"""Tests for pydoclint plugin configuration initialization.

The simplified plugin lets pydoclint read its configuration directly
from [tool.pydoclint] in pyproject.toml. See docs/tool-analysis/pydoclint-analysis.md
for recommended settings.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.tools.pydoclint.definition import PydoclintPlugin


def test_plugin_definition_has_empty_conflicts_with() -> None:
    """Plugin definition has empty conflicts_with list."""
    plugin = PydoclintPlugin()
    assert_that(plugin.definition.conflicts_with).is_empty()


def test_plugin_definition_native_configs() -> None:
    """Plugin definition specifies native config files."""
    plugin = PydoclintPlugin()
    assert_that(plugin.definition.native_configs).contains("pyproject.toml")
    assert_that(plugin.definition.native_configs).contains(".pydoclint.toml")


def test_plugin_default_timeout() -> None:
    """Plugin has default timeout."""
    plugin = PydoclintPlugin()
    assert_that(plugin.definition.default_timeout).is_equal_to(30)
