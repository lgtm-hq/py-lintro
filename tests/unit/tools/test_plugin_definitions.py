"""Parametrized tests for plugin definitions.

These tests consolidate the repetitive definition tests from individual
plugin files, following DRY principles. They verify that each tool plugin
has properly configured definitions.
"""

from __future__ import annotations

from typing import cast

import pytest
from assertpy import assert_that

from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.plugins.base import BaseToolPlugin

# =============================================================================
# Plugin definition metadata for parametrized tests
# =============================================================================

# Each tuple contains:
# (ToolName, plugin_class_path, can_fix, tool_type,
#  description_keywords, native_configs)
PLUGIN_DEFINITIONS: list[tuple[ToolName, str, bool, ToolType, list[str], list[str]]] = [
    (
        ToolName.RUFF,
        "lintro.tools.definitions.ruff.RuffPlugin",
        True,
        ToolType.LINTER | ToolType.FORMATTER,
        ["Python", "linter"],
        ["pyproject.toml", "ruff.toml", ".ruff.toml"],
    ),
    (
        ToolName.BLACK,
        "lintro.tools.definitions.black.BlackPlugin",
        True,
        ToolType.FORMATTER,
        ["Python", "formatter"],
        ["pyproject.toml"],
    ),
    (
        ToolName.HADOLINT,
        "lintro.tools.definitions.hadolint.HadolintPlugin",
        False,
        ToolType.LINTER | ToolType.INFRASTRUCTURE,
        ["Dockerfile", "best practice"],
        [".hadolint.yaml", ".hadolint.yml"],
    ),
    (
        ToolName.MARKDOWNLINT,
        "lintro.tools.definitions.markdownlint.MarkdownlintPlugin",
        False,
        ToolType.LINTER,
        ["Markdown", "linter"],
        [".markdownlint.json", ".markdownlint.yaml", ".markdownlint.yml"],
    ),
    (
        ToolName.YAMLLINT,
        "lintro.tools.definitions.yamllint.YamllintPlugin",
        False,
        ToolType.LINTER,
        ["YAML", "linter"],
        [".yamllint", ".yamllint.yaml", ".yamllint.yml"],
    ),
    (
        ToolName.MYPY,
        "lintro.tools.definitions.mypy.MypyPlugin",
        False,
        ToolType.LINTER | ToolType.TYPE_CHECKER,
        ["type", "Python"],
        ["mypy.ini", "pyproject.toml"],
    ),
    (
        ToolName.BANDIT,
        "lintro.tools.definitions.bandit.BanditPlugin",
        False,
        ToolType.SECURITY,
        ["security", "Python"],
        [".bandit", "pyproject.toml"],
    ),
    (
        ToolName.PYTEST,
        "lintro.tools.definitions.pytest.PytestPlugin",
        False,
        ToolType.TEST_RUNNER,
        ["test"],
        ["pytest.ini", "pyproject.toml"],
    ),
]


def _get_plugin_instance(plugin_class_path: str) -> BaseToolPlugin:
    """Dynamically import and instantiate a plugin class.

    Args:
        plugin_class_path: Full module path to the plugin class.

    Returns:
        An instance of the plugin class.
    """
    module_path, class_name = plugin_class_path.rsplit(".", 1)
    import importlib

    # Safe: module_path comes from hardcoded PLUGIN_DEFINITIONS, not user input
    module = importlib.import_module(module_path)  # nosemgrep: non-literal-import
    plugin_class = getattr(module, class_name)
    return cast(BaseToolPlugin, plugin_class())


# =============================================================================
# Parametrized definition tests
# =============================================================================


@pytest.mark.parametrize(
    (
        "tool_name",
        "plugin_class_path",
        "can_fix",
        "tool_type",
        "keywords",
        "configs",
    ),
    PLUGIN_DEFINITIONS,
    ids=[str(t[0]) for t in PLUGIN_DEFINITIONS],
)
def test_definition_name(
    tool_name: ToolName,
    plugin_class_path: str,
    can_fix: bool,
    tool_type: ToolType,
    keywords: list[str],
    configs: list[str],
) -> None:
    """Each plugin definition has the correct name.

    Args:
        tool_name: The expected tool name.
        plugin_class_path: Full module path to the plugin class.
        can_fix: Whether the tool can fix issues.
        tool_type: The type of tool.
        keywords: Keywords expected in the description.
        configs: Native configuration files supported.
    """
    plugin = _get_plugin_instance(plugin_class_path)
    assert_that(plugin.definition.name).is_equal_to(tool_name)


@pytest.mark.parametrize(
    (
        "tool_name",
        "plugin_class_path",
        "can_fix",
        "tool_type",
        "keywords",
        "configs",
    ),
    PLUGIN_DEFINITIONS,
    ids=[str(t[0]) for t in PLUGIN_DEFINITIONS],
)
def test_definition_description_not_empty(
    tool_name: ToolName,
    plugin_class_path: str,
    can_fix: bool,
    tool_type: ToolType,
    keywords: list[str],
    configs: list[str],
) -> None:
    """Each plugin definition has a non-empty description.

    Args:
        tool_name: The expected tool name.
        plugin_class_path: Full module path to the plugin class.
        can_fix: Whether the tool can fix issues.
        tool_type: The type of tool.
        keywords: Keywords expected in the description.
        configs: Native configuration files supported.
    """
    plugin = _get_plugin_instance(plugin_class_path)
    assert_that(plugin.definition.description).is_not_empty()


@pytest.mark.parametrize(
    (
        "tool_name",
        "plugin_class_path",
        "can_fix",
        "tool_type",
        "keywords",
        "configs",
    ),
    PLUGIN_DEFINITIONS,
    ids=[str(t[0]) for t in PLUGIN_DEFINITIONS],
)
def test_definition_description_contains_keywords(
    tool_name: ToolName,
    plugin_class_path: str,
    can_fix: bool,
    tool_type: ToolType,
    keywords: list[str],
    configs: list[str],
) -> None:
    """Each plugin description contains expected keywords.

    Args:
        tool_name: The expected tool name.
        plugin_class_path: Full module path to the plugin class.
        can_fix: Whether the tool can fix issues.
        tool_type: The type of tool.
        keywords: Keywords expected in the description.
        configs: Native configuration files supported.
    """
    plugin = _get_plugin_instance(plugin_class_path)
    for keyword in keywords:
        assert_that(plugin.definition.description.lower()).contains(keyword.lower())


@pytest.mark.parametrize(
    (
        "tool_name",
        "plugin_class_path",
        "can_fix",
        "tool_type",
        "keywords",
        "configs",
    ),
    PLUGIN_DEFINITIONS,
    ids=[str(t[0]) for t in PLUGIN_DEFINITIONS],
)
def test_definition_can_fix(
    tool_name: ToolName,
    plugin_class_path: str,
    can_fix: bool,
    tool_type: ToolType,
    keywords: list[str],
    configs: list[str],
) -> None:
    """Each plugin definition has correct can_fix value.

    Args:
        tool_name: The expected tool name.
        plugin_class_path: Full module path to the plugin class.
        can_fix: Whether the tool can fix issues.
        tool_type: The type of tool.
        keywords: Keywords expected in the description.
        configs: Native configuration files supported.
    """
    plugin = _get_plugin_instance(plugin_class_path)
    assert_that(plugin.definition.can_fix).is_equal_to(can_fix)


@pytest.mark.parametrize(
    (
        "tool_name",
        "plugin_class_path",
        "can_fix",
        "tool_type",
        "keywords",
        "configs",
    ),
    PLUGIN_DEFINITIONS,
    ids=[str(t[0]) for t in PLUGIN_DEFINITIONS],
)
def test_definition_tool_type(
    tool_name: ToolName,
    plugin_class_path: str,
    can_fix: bool,
    tool_type: ToolType,
    keywords: list[str],
    configs: list[str],
) -> None:
    """Each plugin definition has correct tool_type.

    Args:
        tool_name: The expected tool name.
        plugin_class_path: Full module path to the plugin class.
        can_fix: Whether the tool can fix issues.
        tool_type: The type of tool.
        keywords: Keywords expected in the description.
        configs: Native configuration files supported.
    """
    plugin = _get_plugin_instance(plugin_class_path)
    assert_that(plugin.definition.tool_type).is_equal_to(tool_type)


@pytest.mark.parametrize(
    (
        "tool_name",
        "plugin_class_path",
        "can_fix",
        "tool_type",
        "keywords",
        "configs",
    ),
    PLUGIN_DEFINITIONS,
    ids=[str(t[0]) for t in PLUGIN_DEFINITIONS],
)
def test_definition_has_file_patterns(
    tool_name: ToolName,
    plugin_class_path: str,
    can_fix: bool,
    tool_type: ToolType,
    keywords: list[str],
    configs: list[str],
) -> None:
    """Each plugin definition has non-empty file patterns.

    Args:
        tool_name: The expected tool name.
        plugin_class_path: Full module path to the plugin class.
        can_fix: Whether the tool can fix issues.
        tool_type: The type of tool.
        keywords: Keywords expected in the description.
        configs: Native configuration files supported.
    """
    plugin = _get_plugin_instance(plugin_class_path)
    assert_that(plugin.definition.file_patterns).is_not_empty()


@pytest.mark.parametrize(
    (
        "tool_name",
        "plugin_class_path",
        "can_fix",
        "tool_type",
        "keywords",
        "configs",
    ),
    PLUGIN_DEFINITIONS,
    ids=[str(t[0]) for t in PLUGIN_DEFINITIONS],
)
def test_definition_has_priority(
    tool_name: ToolName,
    plugin_class_path: str,
    can_fix: bool,
    tool_type: ToolType,
    keywords: list[str],
    configs: list[str],
) -> None:
    """Each plugin definition has a valid priority.

    Args:
        tool_name: The expected tool name.
        plugin_class_path: Full module path to the plugin class.
        can_fix: Whether the tool can fix issues.
        tool_type: The type of tool.
        keywords: Keywords expected in the description.
        configs: Native configuration files supported.
    """
    plugin = _get_plugin_instance(plugin_class_path)
    assert_that(plugin.definition.priority).is_greater_than(0)


@pytest.mark.parametrize(
    (
        "tool_name",
        "plugin_class_path",
        "can_fix",
        "tool_type",
        "keywords",
        "configs",
    ),
    PLUGIN_DEFINITIONS,
    ids=[str(t[0]) for t in PLUGIN_DEFINITIONS],
)
def test_definition_has_default_timeout(
    tool_name: ToolName,
    plugin_class_path: str,
    can_fix: bool,
    tool_type: ToolType,
    keywords: list[str],
    configs: list[str],
) -> None:
    """Each plugin definition has a positive default timeout.

    Args:
        tool_name: The expected tool name.
        plugin_class_path: Full module path to the plugin class.
        can_fix: Whether the tool can fix issues.
        tool_type: The type of tool.
        keywords: Keywords expected in the description.
        configs: Native configuration files supported.
    """
    plugin = _get_plugin_instance(plugin_class_path)
    assert_that(plugin.definition.default_timeout).is_greater_than(0)


@pytest.mark.parametrize(
    (
        "tool_name",
        "plugin_class_path",
        "can_fix",
        "tool_type",
        "keywords",
        "configs",
    ),
    PLUGIN_DEFINITIONS,
    ids=[str(t[0]) for t in PLUGIN_DEFINITIONS],
)
def test_definition_native_configs_subset(
    tool_name: ToolName,
    plugin_class_path: str,
    can_fix: bool,
    tool_type: ToolType,
    keywords: list[str],
    configs: list[str],
) -> None:
    """Each plugin definition's native configs contain expected values.

    Args:
        tool_name: The expected tool name.
        plugin_class_path: Full module path to the plugin class.
        can_fix: Whether the tool can fix issues.
        tool_type: The type of tool.
        keywords: Keywords expected in the description.
        configs: Native configuration files supported.
    """
    plugin = _get_plugin_instance(plugin_class_path)
    for config in configs:
        assert_that(plugin.definition.native_configs).contains(config)
