"""Regression tests: Node plugins resolve from the prepared execution directory.

Since #1811 every Node tool resolves ``node_modules/.bin`` before ``PATH``, and
that lookup is anchored on the directory the tool will run in (#1727). The
anchoring lives in each plugin's call site as a ``cwd=ctx.cwd`` argument, which
is exactly the kind of wiring that can be dropped in a refactor without any
other test noticing: resolution would silently fall back to lintro's own working
directory and pick up the wrong project's binary.

These tests fail if that argument goes missing. Version verification is stubbed
so a missing local install cannot skip the plugin before the call site runs
(prettier on CI had no PATH binary and never reached resolution).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.plugins.base import BaseToolPlugin
from lintro.tools.astro_check.definition import AstroCheckPlugin
from lintro.tools.commitlint.definition import CommitlintPlugin
from lintro.tools.markdownlint.definition import MarkdownlintPlugin
from lintro.tools.oxfmt.definition import OxfmtPlugin
from lintro.tools.oxlint.definition import OxlintPlugin
from lintro.tools.prettier.definition import PrettierPlugin
from lintro.tools.stylelint.definition import StylelintPlugin
from lintro.tools.svelte_check.definition import SvelteCheckPlugin
from lintro.tools.tsc.definition import TscPlugin
from lintro.tools.vue_tsc.definition import VueTscPlugin

#: Plugin class, the tool name it resolves, and a benign subprocess payload.
#: stylelint parses its output as JSON, so it gets an empty result array.
NODE_PLUGINS_WITH_FIX: list[tuple[type[BaseToolPlugin], str, str]] = [
    (PrettierPlugin, "prettier", ""),
    (OxlintPlugin, "oxlint", ""),
    (OxfmtPlugin, "oxfmt", ""),
    (StylelintPlugin, "stylelint", "[]"),
]

#: Check-only Node tools (``fix`` raises). Still need the same cwd wiring
#: on the check path. Some of these also resolve without cwd for
#: ``version_command`` while building ``definition``.
NODE_PLUGINS_CHECK_ONLY: list[tuple[type[BaseToolPlugin], str, str]] = [
    (MarkdownlintPlugin, "markdownlint", ""),
    (CommitlintPlugin, "commitlint", ""),
    (SvelteCheckPlugin, "svelte-check", ""),
    (AstroCheckPlugin, "astro-check", ""),
    (TscPlugin, "tsc", ""),
    (VueTscPlugin, "vue-tsc", ""),
]

NODE_PLUGINS_CHECK: list[tuple[type[BaseToolPlugin], str, str]] = (
    NODE_PLUGINS_WITH_FIX + NODE_PLUGINS_CHECK_ONLY
)


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

    fake_result = MagicMock()
    fake_result.success = True
    fake_result.output = output
    fake_result.stdout = output
    fake_result.returncode = 0

    with (
        patch.object(plugin, "_get_executable_command", side_effect=_fake_resolve),
        patch.object(plugin, "_run_subprocess", return_value=(True, output)),
        patch.object(plugin, "_run_subprocess_result", return_value=fake_result),
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
    ):
        getattr(plugin, action)([str(project)], {})

    return calls


@pytest.mark.parametrize(
    ("plugin_cls", "tool_name", "output"),
    NODE_PLUGINS_CHECK,
    ids=[f"tool={name}" for _cls, name, _out in NODE_PLUGINS_CHECK],
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

    _assert_execution_cwd(calls, tool_name=tool_name, project=project)


@pytest.mark.parametrize(
    ("plugin_cls", "tool_name", "output"),
    NODE_PLUGINS_WITH_FIX,
    ids=[f"tool={name}" for _cls, name, _out in NODE_PLUGINS_WITH_FIX],
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

    _assert_execution_cwd(calls, tool_name=tool_name, project=project)


def _assert_execution_cwd(
    calls: list[dict[str, Any]],
    *,
    tool_name: str,
    project: Path,
) -> None:
    """Require a resolution call anchored on the prepared project directory.

    Some plugins also resolve without ``cwd`` while building ``version_command``
    on the ``definition`` property. Those probes are not the execution path.

    Args:
        calls: Keyword arguments of every ``_get_executable_command`` call.
        tool_name: Tool name used in assertion descriptions.
        project: Directory that must appear as ``cwd``.
    """
    assert_that(calls).described_as(f"{tool_name} resolution calls").is_not_empty()
    expected = project.resolve()
    matching = [
        call
        for call in calls
        if call.get("cwd") is not None and Path(call["cwd"]).resolve() == expected
    ]
    assert_that(matching).described_as(
        f"{tool_name} cwd equals the execution directory",
    ).is_not_empty()


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
    (root / "README.md").write_text("# hi\n", encoding="utf-8")
    (root / "App.svelte").write_text("<script></script>\n", encoding="utf-8")
    (root / "Page.astro").write_text("---\n---\n<p></p>\n", encoding="utf-8")
    (root / "index.ts").write_text("const a: number = 1;\n", encoding="utf-8")
    (root / "App.vue").write_text("<template></template>\n", encoding="utf-8")
    return root
