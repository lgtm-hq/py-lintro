"""Guards for the per-tool package layout's re-export shims (#2311).

Tools whose implementation spans several modules live in their own package,
``lintro/tools/<name>/``, with the plugin in ``definition.py``. Plugin
discovery still scans ``lintro/tools/definitions``, so each moved tool leaves a
re-export shim behind there. These tests pin that contract: the shim must
expose the very same plugin class, and importing it must register the tool.
"""

from __future__ import annotations

from types import ModuleType

import pytest
from assertpy import assert_that

from lintro.plugins.registry import ToolRegistry
from lintro.tools.definitions import pytest as pytest_shim
from lintro.tools.definitions import ruff as ruff_shim
from lintro.tools.pytest import definition as pytest_package
from lintro.tools.ruff import definition as ruff_package

#: ``(shim module, package module, plugin attribute, registered tool name)``
#: for every tool that has moved into its own package.
MOVED_TOOLS: list[tuple[ModuleType, ModuleType, str, str]] = [
    (pytest_shim, pytest_package, "PytestPlugin", "pytest"),
    (ruff_shim, ruff_package, "RuffPlugin", "ruff"),
]


@pytest.mark.parametrize(
    ("shim", "package", "plugin_attr", "tool_name"),
    MOVED_TOOLS,
    ids=["pytest", "ruff"],
)
def test_shim_re_exports_the_same_plugin_class(
    shim: ModuleType,
    package: ModuleType,
    plugin_attr: str,
    tool_name: str,
) -> None:
    """The shim exposes the package's plugin class, not a copy of it.

    Args:
        shim: Module under ``lintro.tools.definitions`` that discovery scans.
        package: Module inside the tool's own package.
        plugin_attr: Name of the plugin class both modules expose.
        tool_name: Registered tool name, unused by this assertion.
    """
    del tool_name

    assert_that(getattr(shim, plugin_attr)).is_same_as(
        getattr(package, plugin_attr),
    )


@pytest.mark.parametrize(
    ("shim", "package", "plugin_attr", "tool_name"),
    MOVED_TOOLS,
    ids=["pytest", "ruff"],
)
def test_moved_tool_is_still_discovered(
    shim: ModuleType,
    package: ModuleType,
    plugin_attr: str,
    tool_name: str,
) -> None:
    """Importing the shim registers the tool, so discovery still finds it.

    Args:
        shim: Module under ``lintro.tools.definitions`` that discovery scans.
        package: Module inside the tool's own package, unused here.
        plugin_attr: Name of the plugin class the registry instantiates.
        tool_name: Name the tool is registered under.
    """
    del package

    definitions = ToolRegistry.get_definitions()

    assert_that(definitions).contains_key(tool_name)
    assert_that(type(ToolRegistry.get(tool_name))).is_same_as(
        getattr(shim, plugin_attr),
    )
