"""Pre-execution summary rendering of allowlist-hidden tools (#3066)."""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that
from rich.console import Console

from lintro.utils.console.pre_execution_summary import (
    AllowlistNotice,
    print_pre_execution_summary,
)
from lintro.utils.execution.tool_configuration import SkippedTool


def _render(
    *,
    hidden: list[str],
    verbose: bool,
    skipped: list[SkippedTool] | None = None,
) -> str:
    """Render the summary and return plain text.

    Args:
        hidden: Tools outside the allowlist.
        verbose: Whether verbose output is on.
        skipped: Explicit skip rows.

    Returns:
        str: Recorded console text.
    """
    console = Console(record=True, force_terminal=False, width=160)
    with patch(
        "lintro.utils.console.pre_execution_summary.Console",
        return_value=console,
    ):
        print_pre_execution_summary(
            tools_to_run=["ruff"],
            skipped_tools=skipped or [],
            effective_auto_install=False,
            is_container=False,
            is_ci=False,
            allowlist=AllowlistNotice(names=hidden, verbose=verbose),
        )
    return console.export_text()


def test_hidden_tools_collapse_to_one_count_line() -> None:
    """Outsiders are counted in one line and not named."""
    output = _render(hidden=["mypy", "bandit"], verbose=False)

    assert_that(output).contains(
        "2 tools not in enabled_tools (hidden; use --verbose to list)",
    )
    assert_that(output).does_not_contain("mypy")
    assert_that(output).does_not_contain("Skipped")


def test_single_hidden_tool_is_singular() -> None:
    """One hidden tool reads ``1 tool``."""
    output = _render(hidden=["mypy"], verbose=False)

    assert_that(output).contains("1 tool not in enabled_tools")


def test_verbose_lists_hidden_tool_names() -> None:
    """``--verbose`` names the hidden tools."""
    output = _render(hidden=["mypy", "bandit"], verbose=True)

    assert_that(output).contains("2 tools not in enabled_tools: mypy, bandit")


def test_no_line_without_hidden_tools() -> None:
    """No allowlist exclusions means no count line."""
    output = _render(hidden=[], verbose=False)

    assert_that(output).does_not_contain("not in enabled_tools")


def test_disabled_in_config_row_still_rendered() -> None:
    """Explicit skip rows keep rendering next to the count line."""
    output = _render(
        hidden=["bandit"],
        verbose=False,
        skipped=[SkippedTool(name="mypy", reason="disabled in config")],
    )

    assert_that(output).contains("mypy")
    assert_that(output).contains("disabled in config")
