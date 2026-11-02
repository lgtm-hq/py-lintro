"""Controls for shared check verdicts and the original public import path."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.models.core.tool_outcome import (
    Checked,
    NoFiles,
    Skipped,
    TimedOut,
    ToolOutcome,
    Unavailable,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.utils.check_answered import check_answered

if TYPE_CHECKING:
    from lintro.models.core.tool_outcome import ExecutionOutcome


@pytest.mark.parametrize(
    ("execution", "has_findings", "parse_failures", "answered"),
    [
        pytest.param(None, False, None, False, id="missing-result"),
        pytest.param(Checked(True), False, None, True, id="clean-empty"),
        pytest.param(Checked(False), True, None, True, id="lint-findings"),
        pytest.param(Checked(False), False, None, False, id="failed-empty"),
        pytest.param(Checked(True), False, 1, False, id="incomplete-empty"),
        pytest.param(Checked(False), True, 1, False, id="incomplete-findings"),
        pytest.param(Skipped("version gate"), False, None, False, id="skipped"),
        pytest.param(NoFiles(), False, None, False, id="no-files"),
        pytest.param(TimedOut("deadline"), False, None, False, id="timeout-empty"),
        pytest.param(TimedOut("deadline"), True, None, False, id="timeout-findings"),
        pytest.param(
            Unavailable("missing", strict=False),
            False,
            None,
            False,
            id="unavailable",
        ),
        pytest.param(
            Unavailable("missing", strict=True),
            False,
            None,
            False,
            id="required-missing",
        ),
    ],
)
def test_check_answered_distinguishes_findings_from_missing_measurements(
    execution: ExecutionOutcome | None,
    has_findings: bool,
    parse_failures: int | None,
    answered: bool,
) -> None:
    """A measured empty report answers; failure or incomplete evidence cannot."""
    result = (
        ToolResult.from_outcome(
            ToolOutcome(execution, issues_count=int(has_findings)),
            name="ruff",
            issues=[BaseIssue(file="src/main.py", line=1)] if has_findings else [],
            parse_failures_count=parse_failures,
        )
        if execution is not None
        else None
    )

    assert_that(check_answered(result)).is_equal_to(answered)


def test_verify_pass_reexports_the_shared_helper() -> None:
    """Existing consumers receive the same helper through the original path."""
    from lintro.tools.core.verify_pass import check_answered as original_check_answered

    assert_that(original_check_answered).is_same_as(check_answered)
    assert_that(original_check_answered(ToolResult(success=True))).is_true()
