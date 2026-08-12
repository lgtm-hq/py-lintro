"""Regression tests: Node plugins resolve from the prepared execution directory.

Since #1811 every Node tool resolves ``node_modules/.bin`` before ``PATH``, and
that lookup is anchored on the directory the tool will run in (#1727). The
anchoring lives in each plugin's call site as a ``cwd=ctx.cwd`` argument, which
is exactly the kind of wiring that can be dropped in a refactor without any
other test noticing: resolution would silently fall back to lintro's own working
directory and pick up the wrong project's binary.

These tests fail if that argument goes missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.plugins.base import BaseToolPlugin
from lintro.tools.definitions.oxfmt import OxfmtPlugin
from lintro.tools.definitions.oxlint import OxlintPlugin
from lintro.tools.definitions.prettier import PrettierPlugin
from lintro.tools.definitions.stylelint import StylelintPlugin

#: Plugin class, the tool name it resolves, and a benign subprocess payload.
#: stylelint parses its output as JSON, so it gets an empty result array.
NODE_PLUGINS: list[tuple[type[BaseToolPlugin], str, str]] = [
    (PrettierPlugin, "prettier", ""),
    (OxlintPlugin, "oxlint", ""),
    (OxfmtPlugin, "oxfmt", ""),
    (StylelintPlugin, "stylelint", "[]"),
]


def _record_resolution(
    plugin: BaseToolPlugin,
    project: Path,
    output: str,
    action: str,
) -> list[dict[str, Any]]:
    """Run a plugin action and capture how it resolved its executable.

    Args:
        plugin: Plugin instance under test.
        project: Directory standing in for the checked project.
        output: Payload the faked subprocess returns.
        action: ``"check"`` or ``"fix"``.

    Returns:
        The keyword arguments of every ``_get_executable_command`` call made.
    """
    calls: list[dict[str, Any]] = []

    def _fake_resolve(**kwargs: Any) -> list[str]:
        calls.append(kwargs)
        return ["true"]

    with (
        patch.object(plugin, "_get_executable_command", side_effect=_fake_resolve),
        patch.object(plugin, "_run_subprocess", return_value=(True, output)),
    ):
        getattr(plugin, action)([str(project)], {})

    return calls


@pytest.mark.parametrize(
    ("plugin_cls", "tool_name", "output"),
    NODE_PLUGINS,
    ids=[f"tool={name}" for _cls, name, _out in NODE_PLUGINS],
)
def test_check_resolves_from_the_execution_directory(
    plugin_cls: type[BaseToolPlugin],
    tool_name: str,
    output: str,
    tmp_path: Path,
) -> None:
    """``check`` passes the prepared execution directory into resolution.

    Args:
        plugin_cls: Plugin class under test.
        tool_name: Tool name the plugin resolves.
        output: Payload the faked subprocess returns.
        tmp_path: Temporary directory provided by pytest.
    """
    project = _make_project(tmp_path)
    plugin = plugin_cls()

    calls = _record_resolution(plugin, project, output, "check")

    assert_that(calls).described_as(f"{tool_name} resolution calls").is_not_empty()
    for call in calls:
        assert_that(call).contains_key("cwd")
        assert_that(call["cwd"]).described_as(f"{tool_name} cwd").is_not_none()


@pytest.mark.parametrize(
    ("plugin_cls", "tool_name", "output"),
    NODE_PLUGINS,
    ids=[f"tool={name}" for _cls, name, _out in NODE_PLUGINS],
)
def test_fix_resolves_from_the_execution_directory(
    plugin_cls: type[BaseToolPlugin],
    tool_name: str,
    output: str,
    tmp_path: Path,
) -> None:
    """``fix`` passes the prepared execution directory into resolution.

    The fix path builds its own commands rather than reusing ``check``'s, so it
    can lose the anchoring independently.

    Args:
        plugin_cls: Plugin class under test.
        tool_name: Tool name the plugin resolves.
        output: Payload the faked subprocess returns.
        tmp_path: Temporary directory provided by pytest.
    """
    project = _make_project(tmp_path)
    plugin = plugin_cls()

    calls = _record_resolution(plugin, project, output, "fix")

    assert_that(calls).described_as(f"{tool_name} resolution calls").is_not_empty()
    for call in calls:
        assert_that(call).contains_key("cwd")
        assert_that(call["cwd"]).described_as(f"{tool_name} cwd").is_not_none()


def _make_project(root: Path) -> Path:
    """Create a directory with one file each Node tool will accept.

    Args:
        root: Directory to populate.

    Returns:
        The populated directory.
    """
    (root / "index.js").write_text("const a = 1;\n", encoding="utf-8")
    (root / "styles.css").write_text("a { color: red; }\n", encoding="utf-8")
    (root / "data.json").write_text("{}\n", encoding="utf-8")
    return root
