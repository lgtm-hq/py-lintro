"""Observable contracts for consumers of the frozen outcome variants."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_outcome import (
    Checked,
    FixAccounting,
    FixMeasured,
    FixPartial,
    FixUnmeasured,
    Skipped,
    TimedOut,
    ToolOutcome,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.utils.execution.exit_codes import (
    aggregate_tool_results,
    determine_exit_code,
    unknown_residual_tool_names,
)
from lintro.utils.execution.run_aggregation import sequential_totals
from lintro.utils.execution.run_renderer import _display_fix_result
from lintro.utils.json_output import serialize_tool_result, timed_out_tool_names
from lintro.utils.streaming_output import StreamingResultHandler
from lintro.utils.summary_tables import print_summary_table


@pytest.mark.parametrize(
    ("accounting", "fixed", "remaining", "total_fixed", "total_remaining"),
    [
        (FixMeasured(3, 2, 1), 2, 1, 2, 1),
        (FixPartial(), 0, 0, 0, 0),
        (FixPartial(initial=3), 0, 0, 0, 0),
        (FixPartial(fixed=2), 2, 0, 2, 0),
        (FixPartial(remaining=1), 0, 1, 0, 1),
        (FixUnmeasured("buf verification failed", initial=3), None, None, 0, 0),
        (FixUnmeasured("before-count unavailable"), None, None, 0, 0),
    ],
)
def test_fix_accounting_keeps_wire_defaults_distinct_from_measurements(
    tmp_path: Path,
    accounting: FixAccounting,
    fixed: int | None,
    remaining: int | None,
    total_fixed: int,
    total_remaining: int,
) -> None:
    """JSON keeps legacy defaults while JSONL omits unavailable measurements.

    Args:
        tmp_path: Directory for the streamed JSONL artifact.
        accounting: Reported measurements, including partial and unknown states.
        fixed: Expected legacy JSON fixed value.
        remaining: Expected legacy JSON remaining value.
        total_fixed: Expected sum of supplied fixed measurements.
        total_remaining: Expected sum of supplied residual measurements.
    """
    issue = BaseIssue(file="a.proto", line=1, message="finding")
    result = ToolResult.from_outcome(
        ToolOutcome(Checked(False), accounting, issues_count=1),
        name="buf",
        issues=[issue],
        initial_issues=[issue],
    )
    before = result.outcome
    wire = serialize_tool_result(result, action=Action.FIX)
    target = tmp_path / "results.jsonl"
    with StreamingResultHandler("jsonl", Action.FIX, str(target)) as handler:
        handler.handle_result(result)
    streamed = json.loads(target.read_text())

    assert_that(wire["issues_count"]).is_equal_to(1)
    assert_that(wire["net_resolved"]).is_equal_to(fixed)
    assert_that(wire["fixed"]).is_equal_to(fixed)
    assert_that(wire["remaining"]).is_equal_to(remaining)
    assert_that(streamed.get("initial_issues_count")).is_equal_to(accounting.initial)
    assert_that(streamed.get("net_resolved_count")).is_equal_to(accounting.fixed)
    assert_that(streamed.get("fixed_issues_count")).is_equal_to(accounting.fixed)
    assert_that(streamed.get("remaining_issues_count")).is_equal_to(
        accounting.remaining,
    )
    if accounting.initial is None:
        assert_that(streamed).does_not_contain_key("initial_issues_count")
    if accounting.fixed is None:
        assert_that(streamed).does_not_contain_key(
            "net_resolved_count",
            "fixed_issues_count",
        )
    if accounting.remaining is None:
        assert_that(streamed).does_not_contain_key("remaining_issues_count")
    assert_that(aggregate_tool_results([result], Action.FIX)).is_equal_to(
        (1, total_fixed, total_remaining),
    )
    assert_that(sequential_totals([result], Action.FIX)).is_equal_to(
        (1, total_fixed, total_remaining),
    )
    assert_that(handler.get_totals()).is_equal_to(
        {
            "issues": 1,
            "net_resolved": total_fixed,
            "fixed": total_fixed,
            "remaining": total_remaining,
            "tools_run": 1,
            "tools_failed": 1,
        },
    )
    assert_that(handler.get_exit_code()).is_equal_to(1)
    assert_that(result.outcome).is_equal_to(before)


@pytest.mark.parametrize("execution", [Checked(False), TimedOut("deadline")])
def test_unmeasured_and_timeout_evidence_survive_all_output_consumers(
    execution: Checked | TimedOut,
) -> None:
    """Unknown accounting does not obscure independent execution evidence.

    Args:
        execution: Ordinary failure or an independent execution timeout.
    """
    result = ToolResult.from_outcome(
        ToolOutcome(execution, FixUnmeasured("verify failed", initial=2)),
        name="buf",
    )
    events: list[dict[str, Any]] = []

    def emit(**payload: Any) -> None:
        events.append(payload)

    print_summary_table(emit, Action.FIX, [result])
    _display_fix_result(
        result,
        output_format="grid",
        raw_output=True,
        console_output_func=emit,
        success_func=emit,
        action=Action.FIX,
    )
    text = "\n".join(str(event.get("text", "")) for event in events)
    wire = serialize_tool_result(result, action=Action.FIX)

    assert_that(text).contains("FAIL", "unknown", "residual unknown")
    assert_that(text).does_not_contain("No issues found")
    assert_that(wire["residual_unknown_reason"]).is_equal_to("verify failed")
    assert_that(wire["net_resolved"]).is_none()
    assert_that(wire["remaining"]).is_none()
    assert_that(unknown_residual_tool_names([result])).is_equal_to(["buf"])
    assert_that(timed_out_tool_names([result, result])).is_equal_to(
        ["buf"] if isinstance(execution, TimedOut) else [],
    )
    assert_that(determine_exit_code(Action.FIX, [result], 0, 0)).is_equal_to(1)


def test_consumers_read_completed_legacy_mutations_without_caching_outcomes() -> None:
    """A later skip replaces an earlier measured failure on every new read."""
    result = ToolResult.from_outcome(
        ToolOutcome(Checked(False, inspected_files=False), FixMeasured(2, 1, 1), 1),
        name="ruff",
    )
    original = result.outcome
    assert_that(aggregate_tool_results([result], Action.FIX)).is_equal_to((1, 1, 1))

    # Stage 3 still accepts completed updates through the writable legacy fields.
    result.skipped = True
    result.skip_reason = "tool removed from PATH"
    result.success = True
    result.issues_count = 0
    result.initial_issues_count = None
    result.fixed_issues_count = None
    result.remaining_issues_count = None
    wire = serialize_tool_result(result, action=Action.FIX)

    assert_that(result.outcome.execution).is_equal_to(Skipped("tool removed from PATH"))
    assert_that(original.accounting).is_equal_to(FixMeasured(2, 1, 1))
    assert_that(wire["skipped"]).is_true()
    assert_that(wire["skip_reason"]).is_equal_to("tool removed from PATH")
    assert_that(aggregate_tool_results([result], Action.FIX)).is_equal_to((0, 0, 0))
    assert_that(sequential_totals([result], Action.FIX)).is_equal_to((0, 0, 0))
    assert_that(determine_exit_code(Action.FIX, [result], 0, 0)).is_equal_to(0)
