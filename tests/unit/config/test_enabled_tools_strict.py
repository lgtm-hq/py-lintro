"""Tests for ``execution.enabled_tools`` absent vs empty vs non-empty (#3066)."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.config.config_loader import (
    _parse_execution_config,
    load_config,
    normalize_enabled_tools,
)
from lintro.config.config_validator import validate_config_file
from lintro.config.execution_config import ExecutionConfig
from lintro.config.lintro_config import LintroConfig
from lintro.utils.console.pre_execution_summary import format_no_tools_message


def _write(tmp_path: Path, content: str) -> Path:
    """Write a Lintro YAML config into ``tmp_path``.

    Args:
        tmp_path: Pytest temporary directory.
        content: YAML document body.

    Returns:
        Path: The written config file.
    """
    path = tmp_path / ".lintro-config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_loader_keeps_absent_key_as_none() -> None:
    """An absent key stays ``None`` so it is distinguishable from ``[]``."""
    assert_that(_parse_execution_config({}).enabled_tools).is_none()


def test_loader_passes_non_empty_list_through() -> None:
    """A non-empty list is preserved verbatim."""
    config = _parse_execution_config({"enabled_tools": ["ruff", "mypy"]})

    assert_that(config.enabled_tools).is_equal_to(["ruff", "mypy"])


def test_loader_treats_explicit_empty_list_as_unset() -> None:
    """An explicit ``[]`` behaves like an absent key (all tools)."""
    assert_that(_parse_execution_config({"enabled_tools": []}).enabled_tools).is_none()


def test_load_config_empty_list_does_not_raise(tmp_path: Path) -> None:
    """Loading ``enabled_tools: []`` succeeds and leaves the allowlist unset.

    Args:
        tmp_path: Pytest temporary directory.
    """
    path = _write(tmp_path, "execution:\n  enabled_tools: []\n")

    config = load_config(config_path=str(path))

    assert_that(config.execution.enabled_tools).is_none()
    assert_that(config.is_tool_in_enabled_tools("ruff")).is_true()


def test_validator_warns_on_empty_list(tmp_path: Path) -> None:
    """The validator warns, without failing, on ``enabled_tools: []``.

    Args:
        tmp_path: Pytest temporary directory.
    """
    path = _write(tmp_path, "execution:\n  enabled_tools: []\n")

    result = validate_config_file(path)

    assert_that(result.is_valid).is_true()
    rendered = [w.render() for w in result.warnings]
    assert_that(
        any("is an empty list; it runs all tools" in m for m in rendered),
    ).is_true()
    assert_that(
        any("Omit the key, or list the tools to run." in m for m in rendered),
    ).is_true()


@pytest.mark.parametrize(
    "body",
    [
        "execution:\n  fail_fast: false\n",
        "execution:\n  enabled_tools: [ruff]\n",
    ],
    ids=["absent", "non-empty"],
)
def test_validator_accepts_absent_and_non_empty(tmp_path: Path, body: str) -> None:
    """Absent and non-empty ``enabled_tools`` both validate cleanly.

    Args:
        tmp_path: Pytest temporary directory.
        body: Config body under test.
    """
    result = validate_config_file(_write(tmp_path, body))

    assert_that(result.is_valid).is_true()


def test_none_allowlist_allows_every_tool() -> None:
    """``None`` means no allowlist: every tool is in it."""
    config = LintroConfig(execution=ExecutionConfig())

    assert_that(config.is_tool_in_enabled_tools("ruff")).is_true()


def test_non_empty_allowlist_is_strict_with_alias() -> None:
    """A non-empty list is membership-only and honours hyphen/underscore."""
    config = LintroConfig(
        execution=ExecutionConfig(enabled_tools=["ruff", "markdown_lint"]),
    )

    assert_that(config.is_tool_in_enabled_tools("ruff")).is_true()
    assert_that(config.is_tool_in_enabled_tools("markdown-lint")).is_true()
    assert_that(config.is_tool_in_enabled_tools("mypy")).is_false()


@pytest.mark.parametrize(
    "raw",
    ["", "   ", [], [""], ["  ", ""]],
    ids=["empty-str", "whitespace-str", "empty-list", "blank-entry", "blank-entries"],
)
def test_blank_spellings_are_unset(raw: object) -> None:
    """Empty or blank values normalize to ``None`` through the loader.

    Args:
        raw: Raw ``enabled_tools`` value.
    """
    assert_that(normalize_enabled_tools(raw)).is_none()
    assert_that(_parse_execution_config({"enabled_tools": raw}).enabled_tools).is_none()


def test_blank_entries_beside_real_ones_are_dropped() -> None:
    """Blank list entries are removed and real ones are stripped."""
    config = _parse_execution_config({"enabled_tools": ["ruff", "", " mypy "]})

    assert_that(config.enabled_tools).is_equal_to(["ruff", "mypy"])


def test_non_blank_string_still_becomes_one_entry() -> None:
    """A non-blank string is still wrapped into a one-element list."""
    assert_that(normalize_enabled_tools(" ruff ")).is_equal_to(["ruff"])


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ('execution:\n  enabled_tools: ""\n', "is blank"),
        ('execution:\n  enabled_tools: "  "\n', "is blank"),
        ('execution:\n  enabled_tools: ["", ""]\n', "is blank"),
        ('execution:\n  enabled_tools: [ruff, ""]\n', "has blank entries"),
    ],
    ids=["empty-str", "whitespace-str", "all-blank-list", "mixed-list"],
)
def test_validator_warns_on_blank_spellings(
    tmp_path: Path,
    body: str,
    fragment: str,
) -> None:
    """Blank spellings warn without failing validation.

    Args:
        tmp_path: Pytest temporary directory.
        body: Config body under test.
        fragment: Expected warning text.
    """
    result = validate_config_file(_write(tmp_path, body))

    assert_that(result.is_valid).is_true()
    rendered = [w.render() for w in result.warnings]
    assert_that(any(fragment in m for m in rendered)).is_true()


def test_no_tools_message_names_configured_entries() -> None:
    """The no-tools message reports the exclusion count and configured entries."""
    message = format_no_tools_message(excluded=3, configured=["rufff", "mypy"])

    assert_that(message).contains("3 tools were excluded by execution.enabled_tools")
    assert_that(message).contains("configured: rufff, mypy")
    assert_that(format_no_tools_message(excluded=1, configured=["x"])).contains(
        "1 tool was excluded",
    )
