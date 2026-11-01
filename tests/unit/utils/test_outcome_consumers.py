"""Observable contracts for consumers of the frozen outcome variants."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.metadata import (
    attach_fixed_count_metadata,
    attach_validation_counts_metadata,
)
from lintro.ai.rerun import apply_rerun_results
from lintro.enums.action import Action
from lintro.enums.capability import Cap
from lintro.enums.verify_status import VerifyStatus
from lintro.models.core.tool_outcome import (
    Checked,
    FixAccounting,
    FixMeasured,
    FixPartial,
    FixUnmeasured,
    NoFiles,
    Skipped,
    TimedOut,
    ToolOutcome,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.tools.core.verify_pass import (
    VerifyOutcome,
    VerifyScope,
    fold_verify_results,
)
from lintro.utils.console.logger import ThreadSafeConsoleLogger
from lintro.utils.execution.exit_codes import (
    aggregate_tool_results,
    determine_exit_code,
    unknown_residual_tool_names,
)
from lintro.utils.execution.run_aggregation import sequential_totals
from lintro.utils.execution.run_renderer import _display_fix_result
from lintro.utils.json_output import (
    create_json_output,
    serialize_tool_result,
    timed_out_tool_names,
)
from lintro.utils.streaming_output import StreamingResultHandler
from lintro.utils.summary_tables import print_summary_table


@pytest.mark.parametrize("capability", [None, Cap.CHECK])
def test_fix_json_keeps_default_keys_for_results_without_fix_accounting(
    capability: Cap | None,
) -> None:
    """Check-only and default legacy results retain unconditional FIX keys."""
    result = ToolResult(name="mypy", capability=capability)

    wire = serialize_tool_result(result, action=Action.FIX)

    assert_that(wire).contains_entry(
        {"net_resolved": 0},
        {"fixed": 0},
        {"remaining": 0},
    )
    assert_that(result.outcome.accounting).is_equal_to(FixPartial())
    assert_that(
        serialize_tool_result(result, action=Action.CHECK),
    ).does_not_contain_key(
        "net_resolved",
        "fixed",
        "remaining",
    )


@pytest.mark.parametrize(
    ("previous", "remaining", "expected"),
    [
        (FixMeasured(3, 2, 1), 0, FixPartial(fixed=2, remaining=0)),
        (FixMeasured(3, 2, 1), 1, FixMeasured(3, 2, 1)),
        (FixMeasured(3, 2, 1), 2, FixPartial(fixed=2, remaining=2)),
        (FixMeasured(3, 2, 1), 4, FixPartial(fixed=2, remaining=4)),
        (
            FixUnmeasured("verify failed", initial=3),
            0,
            FixPartial(initial=3, remaining=0),
        ),
        (
            FixUnmeasured("verify failed", initial=3),
            1,
            FixPartial(initial=3, remaining=1),
        ),
        (FixUnmeasured("before-count unavailable"), 0, FixPartial(remaining=0)),
        (FixUnmeasured("before-count unavailable"), 1, FixPartial(remaining=1)),
        (FixPartial(fixed=2), 1, FixPartial(fixed=2, remaining=1)),
    ],
)
def test_ai_rerun_accounting_survives_totals_json_and_exit(
    previous: FixAccounting,
    remaining: int,
    expected: FixAccounting,
) -> None:
    """A measured rerun replaces residual state without inventing before-counts."""
    original = ToolResult.from_outcome(
        ToolOutcome(Checked(False), previous),
        name="ruff",
    )
    issues = [BaseIssue(file="a.py", line=line + 1) for line in range(remaining)]
    rerun = ToolResult.from_outcome(
        ToolOutcome(Checked(remaining == 0, True), issues_count=remaining),
        name="ruff",
        issues=issues,
    )
    by_tool: dict[str, tuple[ToolResult, list[BaseIssue]]] = {"ruff": (original, [])}

    apply_rerun_results(by_tool=by_tool, rerun_results=[rerun])

    assert_that(by_tool["ruff"][0]).is_same_as(original)
    assert_that(original.outcome.accounting).is_equal_to(expected)
    assert_that(original.residual_unknown).is_false()
    assert_that(original.residual_unknown_reason).is_none()
    assert_that(original.issues).is_equal_to(issues)
    totals = (remaining, expected.fixed or 0, remaining)
    assert_that(aggregate_tool_results([original], Action.FIX)).is_equal_to(totals)
    assert_that(sequential_totals([original], Action.FIX)).is_equal_to(totals)
    wire = serialize_tool_result(original, action=Action.FIX)
    assert_that(wire).contains_entry(
        {"net_resolved": expected.fixed or 0},
        {"fixed": expected.fixed or 0},
        {"remaining": remaining},
    )
    assert_that(wire).does_not_contain_key("residual_unknown_reason")
    assert_that(
        determine_exit_code(Action.FIX, [original], remaining, remaining),
    ).is_equal_to(
        int(remaining > 0),
    )


@pytest.mark.parametrize(("native_fixed", "remaining"), [(7, 2), (2, 0)])
def test_ai_rerun_then_metadata_counts_each_fix_once_in_totals_and_json(
    native_fixed: int,
    remaining: int,
) -> None:
    """AI verification is added once, after the native count survives the rerun."""
    ai_verified = 1
    initial = native_fixed + ai_verified + remaining
    detected = [BaseIssue(file="a.py", line=line + 1) for line in range(initial)]
    original = ToolResult.from_outcome(
        ToolOutcome(
            Checked(False, True),
            FixMeasured(initial, native_fixed, remaining + ai_verified),
            remaining + ai_verified,
        ),
        name="ruff",
        initial_issues=detected,
        issues=detected[native_fixed:],
    )
    rerun = ToolResult.from_outcome(
        ToolOutcome(Checked(remaining == 0, True), issues_count=remaining),
        name="ruff",
        issues=detected[native_fixed + ai_verified :],
    )

    apply_rerun_results(
        by_tool={"ruff": (original, list(original.issues))},
        rerun_results=[rerun],
    )
    # The pipeline attaches these counts only after applying the rerun result.
    attach_fixed_count_metadata(original, fixed_count=ai_verified)
    attach_validation_counts_metadata(
        original,
        verified_count=ai_verified,
        unverified_count=0,
    )
    console = ThreadSafeConsoleLogger(art_enabled=False)
    console.print_execution_summary(Action.FIX, [original])
    rows = {
        cells[1].strip(): int(cells[2])
        for line in console.get_buffer().splitlines()
        if len(cells := line.split("|")) == 4 and cells[2].strip().isdigit()
    }
    assert_that(rows).contains_entry(
        {"Net Resolved (Native)": native_fixed},
        {"AI Applied Fixes": ai_verified},
        {"AI Resolved Fixes": ai_verified},
        {"Total Resolved": native_fixed + ai_verified},
        {"Remaining Issues": remaining},
    )
    assert_that(rows["Total Resolved"] + rows["Remaining Issues"]).is_equal_to(initial)
    totals = aggregate_tool_results([original], Action.FIX)
    exit_code = determine_exit_code(Action.FIX, [original], totals[0], totals[2])
    wire = json.loads(
        json.dumps(create_json_output(Action.FIX, [original], *totals, exit_code)),
    )
    assert_that(wire["results"][0]).contains_entry(
        {"net_resolved": native_fixed},
        {"fixed": native_fixed},
        {"remaining": remaining},
        {"issues_count": initial},
    )
    assert_that(wire["results"][0]["metadata"]).contains_entry(
        {"verified_count": ai_verified},
    )
    assert_that(wire["summary"]).contains_entry(
        {"total_net_resolved": native_fixed},
        {"total_fixed": native_fixed},
        {"total_remaining": remaining},
    )
    assert_that(exit_code).is_equal_to(int(remaining > 0))


@pytest.mark.parametrize(
    "status",
    [VerifyStatus.UNKNOWN, VerifyStatus.VERIFIED, VerifyStatus.UNCHANGED],
)
def test_no_files_fold_survives_totals_json_and_exit(status: VerifyStatus) -> None:
    """A failed or answered verification supersedes the empty mutation discovery."""
    mutation = ToolResult.from_outcome(ToolOutcome(NoFiles()), name="ruff")
    verify = (
        ToolResult.from_outcome(
            ToolOutcome(Checked(False, True), issues_count=1),
            name="ruff",
            issues=[BaseIssue(file="/repo/a.py", line=1)],
        )
        if status is VerifyStatus.VERIFIED
        else None
    )
    results = [mutation]
    fold_verify_results(
        mutation_results=results,
        verify_results=[
            VerifyOutcome(
                tool="ruff",
                status=status,
                result=verify,
                unknown_reason="check failed" if status is VerifyStatus.UNKNOWN else "",
            ),
        ],
        scope=VerifyScope(files=("/repo/a.py",), narrowed=True),
    )

    folded = results[0]
    unknown = status is VerifyStatus.UNKNOWN
    remaining = int(status is VerifyStatus.VERIFIED)
    assert_that(folded.outcome.success).is_equal_to(status is VerifyStatus.UNCHANGED)
    assert_that(folded.no_files).is_equal_to(status is VerifyStatus.UNCHANGED)
    assert_that(aggregate_tool_results(results, Action.FIX)).is_equal_to(
        (remaining, 0, remaining),
    )
    assert_that(sequential_totals(results, Action.FIX)).is_equal_to(
        (remaining, 0, remaining),
    )
    wire = serialize_tool_result(folded, action=Action.FIX)
    assert_that(wire).contains_entry(
        {"net_resolved": None if unknown else 0},
        {"fixed": None if unknown else 0},
        {"remaining": None if unknown else remaining},
    )
    assert_that(unknown_residual_tool_names(results)).is_equal_to(
        ["ruff"] if unknown else [],
    )
    assert_that(
        determine_exit_code(Action.FIX, results, remaining, remaining),
    ).is_equal_to(
        int(status is not VerifyStatus.UNCHANGED),
    )


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


def test_answered_ai_rerun_replaces_timeout_and_streams_partial_native_accounting(
    tmp_path: Path,
) -> None:
    """A fresh verdict clears timeout and omits an initial total that no longer adds up."""
    result = ToolResult.from_outcome(
        ToolOutcome(TimedOut("deadline"), FixMeasured(10, 7, 3), issues_count=3),
        name="ruff",
    )
    issue = BaseIssue(file="a.py", line=1, message="remaining")
    rerun = ToolResult(name="ruff", success=False, issues_count=1, issues=[issue])

    apply_rerun_results(by_tool={"ruff": (result, [])}, rerun_results=[rerun])

    target = tmp_path / "rerun.jsonl"
    with StreamingResultHandler("jsonl", Action.FIX, str(target)) as handler:
        handler.handle_result(result)
    streamed = json.loads(target.read_text())
    assert_that(result.outcome.execution).is_equal_to(Checked(False))
    assert_that(result.outcome.accounting).is_equal_to(FixPartial(fixed=7, remaining=1))
    assert_that(timed_out_tool_names([result])).is_empty()
    assert_that(streamed).does_not_contain_key("initial_issues_count")
    assert_that(streamed).contains_entry(
        {"net_resolved_count": 7},
        {"fixed_issues_count": 7},
        {"remaining_issues_count": 1},
    )
    assert_that(handler.get_exit_code()).is_equal_to(1)


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
