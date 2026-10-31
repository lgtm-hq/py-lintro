"""Output consumers require the canonical result's explicit success verdict."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.utils.console.logger import ThreadSafeConsoleLogger
from lintro.utils.execution.exit_codes import determine_exit_code
from lintro.utils.json_output import serialize_tool_result
from lintro.utils.summary_tables import print_summary_table


@pytest.mark.parametrize(
    "consume",
    [
        lambda result: serialize_tool_result(result, action=Action.CHECK),
        lambda result: determine_exit_code(Action.CHECK, [result], 0, 0),
        lambda result: print_summary_table(
            ThreadSafeConsoleLogger().console_output,
            Action.CHECK,
            [result],
        ),
        lambda result: ThreadSafeConsoleLogger().print_execution_summary(
            Action.CHECK,
            [result],
        ),
        lambda result: ThreadSafeConsoleLogger().print_execution_summary(
            Action.FIX,
            [result],
        ),
    ],
    ids=["json", "exit-code", "summary-table", "console-check", "console-fix"],
)
def test_missing_success_cannot_be_reported_as_a_pass(
    consume: Callable[[ToolResult], object],
) -> None:
    """A malformed result must not acquire a successful verdict on read.

    Args:
        consume: Result consumer at the output or exit-code boundary.
    """
    fields = vars(ToolResult(name="incomplete")).copy()
    del fields["success"]
    malformed = cast(ToolResult, SimpleNamespace(**fields))

    assert_that(consume).raises(AttributeError).when_called_with(malformed)
