"""Integration: a three-tool allowlist against the full tool registry (#3066)."""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that
from rich.console import Console

from lintro.config.config_loader import clear_config_cache
from lintro.config.execution_config import ExecutionConfig
from lintro.config.lintro_config import LintroConfig
from lintro.tools import tool_manager
from lintro.utils.console.pre_execution_summary import (
    AllowlistNotice,
    print_pre_execution_summary,
)
from lintro.utils.execution.tool_configuration import get_tools_to_run

ALLOWLIST = ["ruff", "mypy", "bandit"]


def test_three_tool_allowlist_yields_three_sections_and_no_skip_table() -> None:
    """Only the listed tools run and the summary has no skip table."""
    clear_config_cache()
    config = LintroConfig(execution=ExecutionConfig(enabled_tools=ALLOWLIST))

    with patch(
        "lintro.utils.execution.tool_configuration.get_config",
        return_value=config,
    ):
        result = get_tools_to_run(tools=None, action="check")

    assert_that(sorted(result.to_run)).is_equal_to(sorted(ALLOWLIST))
    assert_that(result.skipped).is_empty()
    # The rest of the real registry is counted, not listed.
    assert_that(len(result.not_in_allowlist)).is_greater_than(3)
    assert_that(tool_manager.get_check_tools()).contains(*ALLOWLIST)

    console = Console(record=True, force_terminal=False, width=160)
    with patch(
        "lintro.utils.console.pre_execution_summary.Console",
        return_value=console,
    ):
        print_pre_execution_summary(
            tools_to_run=result.to_run,
            skipped_tools=result.skipped,
            effective_auto_install=False,
            is_container=False,
            is_ci=False,
            allowlist=AllowlistNotice(names=result.not_in_allowlist),
        )
    output = console.export_text()

    for name in ALLOWLIST:
        assert_that(output).contains(f"• {name}")
    assert_that(output).does_not_contain("Skipped")
    assert_that(output).contains("not in enabled_tools (hidden;")
