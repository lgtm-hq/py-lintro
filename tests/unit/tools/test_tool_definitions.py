"""Consolidated tests for all tool definitions.

This module provides parametrized tests to verify that all tool plugins
have correct definition properties. This ensures consistency across the
codebase and catches definition errors early.
"""

from __future__ import annotations

from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.tool_name import ToolName

# Tool specifications: (name, class, timeout, priority, can_fix)
# Some tools need special initialization (mocked dependencies)
TOOL_SPECS = [
    # (tool_name, module_path, class_name, timeout, priority, can_fix)
    (ToolName.RUFF, "lintro.tools.definitions.ruff", "RuffPlugin", 30, 85, True),
    (ToolName.BLACK, "lintro.tools.definitions.black", "BlackPlugin", 30, 90, True),
    (ToolName.CLIPPY, "lintro.tools.definitions.clippy", "ClippyPlugin", 120, 85, True),
    (ToolName.MYPY, "lintro.tools.definitions.mypy", "MypyPlugin", 60, 82, False),
    (
        ToolName.YAMLLINT,
        "lintro.tools.definitions.yamllint",
        "YamllintPlugin",
        15,
        40,
        False,
    ),
    (
        ToolName.HADOLINT,
        "lintro.tools.definitions.hadolint",
        "HadolintPlugin",
        30,
        50,
        False,
    ),
    (
        ToolName.PYTEST,
        "lintro.tools.definitions.pytest",
        "PytestPlugin",
        300,
        90,
        False,
    ),
    (
        ToolName.MARKDOWNLINT,
        "lintro.tools.definitions.markdownlint",
        "MarkdownlintPlugin",
        30,
        30,
        False,
    ),
    (
        ToolName.ACTIONLINT,
        "lintro.tools.definitions.actionlint",
        "ActionlintPlugin",
        30,
        40,
        False,
    ),
    (ToolName.BANDIT, "lintro.tools.definitions.bandit", "BanditPlugin", 30, 90, False),
    (
        ToolName.CHECKOV,
        "lintro.tools.definitions.checkov",
        "CheckovPlugin",
        120,
        88,
        False,
    ),
]


def _create_plugin_instance(module_path: str, class_name: str) -> Any:
    """Dynamically import and instantiate a plugin class.

    Args:
        module_path: Full module path to the plugin class.
        class_name: Name of the plugin class.

    Returns:
        An instance of the plugin class.
    """
    import importlib

    # Safe: module_path comes from hardcoded TOOL_SPECS, not user input
    module = importlib.import_module(module_path)  # nosemgrep: non-literal-import
    plugin_class = getattr(module, class_name)
    return plugin_class()


# =============================================================================
# Tests for tool definition properties
# =============================================================================


@pytest.mark.parametrize(
    (
        "tool_name",
        "module_path",
        "class_name",
        "expected_timeout",
        "expected_priority",
        "expected_can_fix",
    ),
    TOOL_SPECS,
    ids=[spec[0] for spec in TOOL_SPECS],
)
def test_tool_definition_name(
    tool_name: str,
    module_path: str,
    class_name: str,
    expected_timeout: int,
    expected_priority: int,
    expected_can_fix: bool,
) -> None:
    """Tool has correct name in definition.

    Args:
        tool_name: Name of the tool.
        module_path: Module path containing the tool plugin.
        class_name: Class name of the tool plugin.
        expected_timeout: Expected timeout value for the tool.
        expected_priority: Expected priority value for the tool.
        expected_can_fix: Whether the tool can perform fixes.
    """
    plugin = _create_plugin_instance(module_path, class_name)
    assert_that(plugin.definition.name).is_equal_to(tool_name)


@pytest.mark.parametrize(
    (
        "tool_name",
        "module_path",
        "class_name",
        "expected_timeout",
        "expected_priority",
        "expected_can_fix",
    ),
    TOOL_SPECS,
    ids=[spec[0] for spec in TOOL_SPECS],
)
def test_tool_definition_has_description(
    tool_name: str,
    module_path: str,
    class_name: str,
    expected_timeout: int,
    expected_priority: int,
    expected_can_fix: bool,
) -> None:
    """Tool has non-empty description in definition.

    Args:
        tool_name: Name of the tool.
        module_path: Module path containing the tool plugin.
        class_name: Class name of the tool plugin.
        expected_timeout: Expected timeout value for the tool.
        expected_priority: Expected priority value for the tool.
        expected_can_fix: Whether the tool can perform fixes.
    """
    plugin = _create_plugin_instance(module_path, class_name)
    assert_that(plugin.definition.description).is_not_empty()


@pytest.mark.parametrize(
    (
        "tool_name",
        "module_path",
        "class_name",
        "expected_timeout",
        "expected_priority",
        "expected_can_fix",
    ),
    TOOL_SPECS,
    ids=[spec[0] for spec in TOOL_SPECS],
)
def test_tool_definition_timeout(
    tool_name: str,
    module_path: str,
    class_name: str,
    expected_timeout: int,
    expected_priority: int,
    expected_can_fix: bool,
) -> None:
    """Tool has correct default timeout.

    Args:
        tool_name: Name of the tool.
        module_path: Module path containing the tool plugin.
        class_name: Class name of the tool plugin.
        expected_timeout: Expected timeout value for the tool.
        expected_priority: Expected priority value for the tool.
        expected_can_fix: Whether the tool can perform fixes.
    """
    plugin = _create_plugin_instance(module_path, class_name)
    assert_that(plugin.definition.default_timeout).is_equal_to(expected_timeout)


@pytest.mark.parametrize(
    (
        "tool_name",
        "module_path",
        "class_name",
        "expected_timeout",
        "expected_priority",
        "expected_can_fix",
    ),
    TOOL_SPECS,
    ids=[spec[0] for spec in TOOL_SPECS],
)
def test_tool_definition_priority(
    tool_name: str,
    module_path: str,
    class_name: str,
    expected_timeout: int,
    expected_priority: int,
    expected_can_fix: bool,
) -> None:
    """Tool has correct priority.

    Args:
        tool_name: Name of the tool.
        module_path: Module path containing the tool plugin.
        class_name: Class name of the tool plugin.
        expected_timeout: Expected timeout value for the tool.
        expected_priority: Expected priority value for the tool.
        expected_can_fix: Whether the tool can perform fixes.
    """
    plugin = _create_plugin_instance(module_path, class_name)
    assert_that(plugin.definition.priority).is_equal_to(expected_priority)


@pytest.mark.parametrize(
    (
        "tool_name",
        "module_path",
        "class_name",
        "expected_timeout",
        "expected_priority",
        "expected_can_fix",
    ),
    TOOL_SPECS,
    ids=[spec[0] for spec in TOOL_SPECS],
)
def test_tool_definition_can_fix(
    tool_name: str,
    module_path: str,
    class_name: str,
    expected_timeout: int,
    expected_priority: int,
    expected_can_fix: bool,
) -> None:
    """Tool has correct can_fix value.

    Args:
        tool_name: Name of the tool.
        module_path: Module path containing the tool plugin.
        class_name: Class name of the tool plugin.
        expected_timeout: Expected timeout value for the tool.
        expected_priority: Expected priority value for the tool.
        expected_can_fix: Whether the tool can perform fixes.
    """
    plugin = _create_plugin_instance(module_path, class_name)
    assert_that(plugin.definition.can_fix).is_equal_to(expected_can_fix)


# =============================================================================
# Tests for tool definition validation
# =============================================================================


@pytest.mark.parametrize(
    (
        "tool_name",
        "module_path",
        "class_name",
        "expected_timeout",
        "expected_priority",
        "expected_can_fix",
    ),
    TOOL_SPECS,
    ids=[spec[0] for spec in TOOL_SPECS],
)
def test_tool_has_file_patterns(
    tool_name: str,
    module_path: str,
    class_name: str,
    expected_timeout: int,
    expected_priority: int,
    expected_can_fix: bool,
) -> None:
    """Tool has non-empty file patterns.

    Args:
        tool_name: Name of the tool.
        module_path: Module path containing the tool plugin.
        class_name: Class name of the tool plugin.
        expected_timeout: Expected timeout value for the tool.
        expected_priority: Expected priority value for the tool.
        expected_can_fix: Whether the tool can perform fixes.
    """
    plugin = _create_plugin_instance(module_path, class_name)
    assert_that(plugin.definition.file_patterns).is_not_empty()


@pytest.mark.parametrize(
    (
        "tool_name",
        "module_path",
        "class_name",
        "expected_timeout",
        "expected_priority",
        "expected_can_fix",
    ),
    TOOL_SPECS,
    ids=[spec[0] for spec in TOOL_SPECS],
)
def test_tool_has_default_options(
    tool_name: str,
    module_path: str,
    class_name: str,
    expected_timeout: int,
    expected_priority: int,
    expected_can_fix: bool,
) -> None:
    """Tool has default options dictionary.

    Args:
        tool_name: Name of the tool.
        module_path: Module path containing the tool plugin.
        class_name: Class name of the tool plugin.
        expected_timeout: Expected timeout value for the tool.
        expected_priority: Expected priority value for the tool.
        expected_can_fix: Whether the tool can perform fixes.
    """
    plugin = _create_plugin_instance(module_path, class_name)
    assert_that(plugin.definition.default_options).is_not_none()
    assert_that(plugin.definition.default_options).contains_key("timeout")
