"""Tests for changed-file to tool mapping (smart tool selection)."""

from __future__ import annotations

from typing import Callable

from assertpy import assert_that

from lintro.watch.tool_selection import get_tools_for_file, select_tools_for_files

ToolBuilder = Callable[[dict[str, list[str]]], dict[str, object]]


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
    """A tool with a ``*`` pattern matches any file."""
    tools = make_tools({"gitleaks": ["*"], "ruff": ["*.py"]})

    assert_that(
        get_tools_for_file("README.md", available_tools=tools),
    ).is_equal_to(["gitleaks"])


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
