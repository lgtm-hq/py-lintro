"""Tests for changed-file to tool mapping (smart tool selection)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from assertpy import assert_that

from lintro.plugins.base import BaseToolPlugin
from lintro.watch.tool_selection import get_tools_for_file, select_tools_for_files

ToolBuilder = Callable[[dict[str, list[str]]], dict[str, BaseToolPlugin]]


def test_python_file_selects_python_tools(make_tools: ToolBuilder) -> None:
    """A .py file selects tools whose patterns match it."""
    tools = make_tools(
        {
            "ruff": ["*.py", "*.pyi"],
            "mypy": ["*.py", "*.pyi"],
            "oxlint": ["*.ts", "*.tsx"],
        },
    )

    selected = get_tools_for_file("src/foo.py", available_tools=tools)

    assert_that(selected).is_equal_to(["mypy", "ruff"])


def test_typescript_file_excludes_python_tools(make_tools: ToolBuilder) -> None:
    """A .ts file selects only TS-matching tools."""
    tools = make_tools(
        {
            "ruff": ["*.py"],
            "oxlint": ["*.ts", "*.tsx"],
        },
    )

    selected = get_tools_for_file("app/index.ts", available_tools=tools)

    assert_that(selected).is_equal_to(["oxlint"])


def test_basename_patterns_match(make_tools: ToolBuilder) -> None:
    """Non-extension basename globs (e.g. Dockerfile.*) match correctly."""
    tools = make_tools(
        {
            "hadolint": ["Dockerfile", "Dockerfile.*"],
            "ruff": ["*.py"],
        },
    )

    assert_that(
        get_tools_for_file("build/Dockerfile", available_tools=tools),
    ).is_equal_to(["hadolint"])
    assert_that(
        get_tools_for_file("build/Dockerfile.prod", available_tools=tools),
    ).is_equal_to(["hadolint"])


def test_wildcard_pattern_matches_everything(make_tools: ToolBuilder) -> None:
    """A tool with a ``*`` pattern matches any file at the raw layer."""
    tools = make_tools({"gitleaks": ["*"], "ruff": ["*.py"]})

    assert_that(
        get_tools_for_file("README.md", available_tools=tools),
    ).is_equal_to(["gitleaks"])


def test_smart_selection_excludes_catch_all_unless_named(
    make_tools: ToolBuilder,
) -> None:
    """Catch-all ``*`` tools stay out of default watch batches."""
    tools = make_tools({"catchall": ["*"], "ruff": ["*.py"]})

    assert_that(
        select_tools_for_files(["README.md", "foo.py"], available_tools=tools),
    ).is_equal_to(["ruff"])
    assert_that(
        select_tools_for_files(
            ["README.md"],
            restrict_to=["catchall"],
            available_tools=tools,
        ),
    ).is_equal_to(["catchall"])


def test_no_match_returns_empty(make_tools: ToolBuilder) -> None:
    """A file matching no tool returns an empty list."""
    tools = make_tools({"ruff": ["*.py"]})

    assert_that(
        get_tools_for_file("data.csv", available_tools=tools),
    ).is_empty()


def test_empty_patterns_never_match(make_tools: ToolBuilder) -> None:
    """A tool with no patterns is never selected."""
    tools = make_tools({"osv_scanner": [], "ruff": ["*.py"]})

    assert_that(
        get_tools_for_file("foo.py", available_tools=tools),
    ).is_equal_to(["ruff"])


def test_select_for_files_unions_across_batch(make_tools: ToolBuilder) -> None:
    """Selection over a batch unions the per-file matches."""
    tools = make_tools(
        {
            "ruff": ["*.py"],
            "oxlint": ["*.ts"],
            "yamllint": ["*.yaml", "*.yml"],
        },
    )

    selected = select_tools_for_files(
        ["a.py", "b.ts", "c.py"],
        available_tools=tools,
    )

    assert_that(selected).is_equal_to(["oxlint", "ruff"])


def test_restrict_to_intersects_with_matches(make_tools: ToolBuilder) -> None:
    """restrict_to keeps only matched tools that are also allowlisted."""
    tools = make_tools(
        {
            "ruff": ["*.py"],
            "mypy": ["*.py"],
            "bandit": ["*.py"],
        },
    )

    selected = select_tools_for_files(
        ["a.py"],
        restrict_to=["ruff", "mypy"],
        available_tools=tools,
    )

    assert_that(selected).is_equal_to(["mypy", "ruff"])


def test_restrict_to_is_case_insensitive(make_tools: ToolBuilder) -> None:
    """restrict_to matching ignores case."""
    tools = make_tools({"ruff": ["*.py"], "mypy": ["*.py"]})

    selected = select_tools_for_files(
        ["a.py"],
        restrict_to=["RUFF"],
        available_tools=tools,
    )

    assert_that(selected).is_equal_to(["ruff"])


@pytest.mark.parametrize(
    ("registered_name", "requested_name", "filename"),
    [
        ("svelte-check", "svelte_check", "Component.svelte"),
        ("vue-tsc", "vue_tsc", "Component.vue"),
        ("astro-check", "astro_check", "Page.astro"),
    ],
)
def test_restrict_to_accepts_underscore_aliases(
    make_tools: ToolBuilder,
    registered_name: str,
    requested_name: str,
    filename: str,
) -> None:
    """Underscore aliases select tools registered with hyphenated names."""
    tools = make_tools({registered_name: [f"*{Path(filename).suffix}"]})

    selected = select_tools_for_files(
        [filename],
        restrict_to=[requested_name],
        available_tools=tools,
    )

    assert_that(selected).is_equal_to([registered_name])


def test_restrict_to_that_excludes_all_returns_empty(
    make_tools: ToolBuilder,
) -> None:
    """An allowlist with no matching tools yields nothing to run."""
    tools = make_tools({"ruff": ["*.py"]})

    selected = select_tools_for_files(
        ["a.py"],
        restrict_to=["oxlint"],
        available_tools=tools,
    )

    assert_that(selected).is_empty()


def test_selection_is_sorted_and_deduplicated(make_tools: ToolBuilder) -> None:
    """Duplicate matches across files are collapsed and sorted."""
    tools = make_tools({"ruff": ["*.py"], "mypy": ["*.py"]})

    selected = select_tools_for_files(
        ["a.py", "b.py", "c.py"],
        available_tools=tools,
    )

    assert_that(selected).is_equal_to(["mypy", "ruff"])


def test_advisory_and_pytest_are_dropped_from_smart_selection() -> None:
    """Advisory finders and pytest must not be passed to the check executor."""
    from tests.unit.watch.conftest import _FakeDefinition, _FakePlugin

    tools = cast(
        dict[str, BaseToolPlugin],
        {
            "ruff": _FakePlugin(definition=_FakeDefinition(file_patterns=["*.py"])),
            "idiom-review": _FakePlugin(
                definition=_FakeDefinition(
                    file_patterns=["*.py"],
                    is_advisory=True,
                ),
            ),
            "pytest": _FakePlugin(
                definition=_FakeDefinition(file_patterns=["test_*.py", "*.py"]),
            ),
        },
    )

    selected = select_tools_for_files(
        ["foo.py", "test_foo.py"],
        available_tools=tools,
    )

    assert_that(selected).is_equal_to(["ruff"])


def test_auto_fix_keeps_only_fixable_tools() -> None:
    """``--fix`` must not select tools that cannot format."""
    from tests.unit.watch.conftest import _FakeDefinition, _FakePlugin

    tools = cast(
        dict[str, BaseToolPlugin],
        {
            "ruff": _FakePlugin(
                definition=_FakeDefinition(file_patterns=["*.py"], can_fix=True),
            ),
            "mypy": _FakePlugin(
                definition=_FakeDefinition(file_patterns=["*.py"], can_fix=False),
            ),
        },
    )

    selected = select_tools_for_files(
        ["foo.py"],
        available_tools=tools,
        auto_fix=True,
    )

    assert_that(selected).is_equal_to(["ruff"])


def test_live_python_selection_is_executor_compatible() -> None:
    """Live registry selection must not raise in get_tools_to_run.

    This is the default ``lintro watch`` path: a ``.py`` save plus the real
    plugin registry, then the same executor contract ``check`` uses.
    """
    from lintro.enums.action import Action
    from lintro.utils.execution.tool_configuration import get_tools_to_run

    selected = select_tools_for_files(["src/foo.py"])

    assert_that(selected).contains("ruff")
    assert_that("idiom-review" in selected or "idiom_review" in selected).is_false()
    assert_that(selected).does_not_contain("gitleaks")
    assert_that(selected).does_not_contain("typos")
    assert_that(selected).does_not_contain("trufflehog")
    assert_that(selected).does_not_contain("commitlint")
    assert_that(selected).does_not_contain("pytest")

    result = get_tools_to_run(
        tools=",".join(selected),
        action=Action.CHECK,
        scan_roots=["src/foo.py"],
    )
    assert_that(result.to_run).contains("ruff")


def test_live_fix_selection_is_executor_compatible() -> None:
    """Live ``--fix`` selection must only include formatters the executor accepts."""
    from lintro.enums.action import Action
    from lintro.utils.execution.tool_configuration import get_tools_to_run

    selected = select_tools_for_files(["src/foo.py"], auto_fix=True)

    assert_that(selected).contains("ruff")
    result = get_tools_to_run(
        tools=",".join(selected),
        action=Action.FIX,
        scan_roots=["src/foo.py"],
    )
    assert_that(result.to_run).contains("ruff")
