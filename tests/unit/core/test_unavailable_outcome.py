"""Unavailable tools preserve frozen execution and absent measurements."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.tool_run_status import ToolRunStatus, tool_run_status
from lintro.models.core.tool_outcome import Checked, ToolOutcome, Unavailable
from lintro.models.core.tool_result import ToolResult
from lintro.utils.execution.exit_codes import (
    aggregate_tool_results,
    determine_exit_code,
)
from lintro.utils.json_output import serialize_tool_result
from lintro.utils.summary_tables import print_summary_table


@pytest.mark.parametrize("strict", [False, True])
def test_unavailable_policy_preserves_status_and_unknown_measurements(
    strict: bool,
) -> None:
    """Strict policy changes exit status while keeping the probe diagnosis.

    Args:
        strict: Whether missing tools should fail this run.
    """
    outcome = ToolOutcome(Unavailable("binary missing", strict=strict))
    result = ToolResult.from_outcome(outcome, name="ghost", output="Install ghost")
    wire = serialize_tool_result(result, action=Action.FIX)
    lines: list[str] = []
    print_summary_table(lambda text: lines.append(text), Action.FIX, [result])

    assert_that(result.outcome).is_equal_to(outcome)
    assert_that(result.skipped).is_false()
    assert_that(result.skip_reason).is_none()
    assert_that(wire).contains_entry(
        {"status": "unavailable"},
        {"unavailable_reason": "binary missing"},
        {"remaining": None},
        {"fixed": None},
        {"net_resolved": None},
    )
    assert_that(tool_run_status(result=result, issue_count=0)).is_equal_to(
        ToolRunStatus.UNAVAILABLE,
    )
    assert_that(aggregate_tool_results([result], Action.FIX)).is_equal_to((0, 0, 0))
    assert_that(determine_exit_code(Action.FIX, [result], 0, 0)).is_equal_to(
        int(strict),
    )
    assert_that("".join(lines)).contains("UNAVAIL", "binary missing")
    assert_that(result.inspected_files).is_false()


def test_replacing_unavailable_outcome_clears_stale_probe_evidence() -> None:
    """A recovered tool gets a checked replacement without mutating its snapshot."""
    missing = ToolResult.from_outcome(ToolOutcome(Unavailable("not installed")))
    recovered = missing.with_outcome(ToolOutcome(Checked(True, inspected_files=True)))

    assert_that(missing.unavailable).is_true()
    assert_that(recovered.unavailable).is_false()
    assert_that(recovered.unavailable_reason).is_none()
    assert_that(recovered.inspected_files).is_true()
    assert_that(recovered.success).is_true()
    untyped_result: Any = missing
    with pytest.raises(FrozenInstanceError):
        untyped_result.unavailable_reason = None


@pytest.mark.parametrize(
    "changes",
    [
        {"skipped": True, "skip_reason": "disabled"},
        {"timed_out": True},
        {"no_files": True},
        {"inspected_files": True},
        {"issues_count": 1},
        {"unavailable_reason": " "},
    ],
)
def test_unavailable_replacements_reject_conflicting_evidence(
    changes: dict[str, Any],
) -> None:
    """Unavailable execution cannot also claim inspection, findings or another state.

    Args:
        changes: Contradictory replacement fields.
    """
    missing = ToolResult.from_outcome(ToolOutcome(Unavailable("no executable")))
    with pytest.raises(ValueError):
        replace(missing, **changes)
    assert_that(missing.outcome.execution).is_equal_to(Unavailable("no executable"))
