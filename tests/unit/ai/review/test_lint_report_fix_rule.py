"""A fix report's ``remaining`` is read by one closed rule (#3497).

``remaining`` is a fix verdict only on a fix row: the writer emits it with
``fixed`` and ``net_resolved`` on every fix row and never on a check row. It
must fit the findings the entry lists, a check report may not carry it, and
it never overrides incomplete evidence. Every case runs the real load,
restrict and format path.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.review.lint_bridge import (
    LintReportError,
    LintReportIssue,
    format_lint_results_for_prompt,
    load_lint_report,
    restrict_lint_results_to_files,
)
from lintro.ai.review.lint_completeness import LintRunCounts, count_lint_results
from lintro.enums.action import Action
from lintro.enums.capability import Cap
from lintro.models.core.tool_outcome import (
    Checked,
    FixUnmeasured,
    ToolOutcome,
    Unavailable,
)
from lintro.models.core.tool_result import ToolResult
from lintro.utils.json_output import serialize_tool_result

_CHANGED = "src/app.py"
_OTHER = "src/other.py"


def _issue(path: str, message: str, line: int = 3) -> LintReportIssue:
    """Build a finding.

    Args:
        path: File it names.
        message: Its message.
        line: Its line.

    Returns:
        The finding.
    """
    return LintReportIssue(file=path, line=line, message=message, code="X1")


def _wire(issue: LintReportIssue) -> dict[str, Any]:
    """Serialise a finding the way the report writer does.

    Args:
        issue: The finding.

    Returns:
        Its report object.
    """
    return {
        "file": issue.file,
        "line": issue.line,
        "code": issue.code,
        "message": issue.message,
    }


def _digest(
    tmp_path: Path,
    entries: list[dict[str, Any]],
    *,
    action: str | None = None,
) -> tuple[str, LintRunCounts]:
    """Write a report and read it as ``--lint-report`` does.

    Args:
        tmp_path: Pytest temporary directory.
        entries: Per-tool report objects.
        action: The document's ``action``, or None to omit it as the stdout
            writer does.

    Returns:
        The digest and the counts.
    """
    document: dict[str, Any] = {"results": entries}
    if action is not None:
        document["action"] = action
    report = tmp_path / "results.json"
    report.write_text(json.dumps(document), encoding="utf-8")
    reloaded = restrict_lint_results_to_files(
        results=load_lint_report(report),
        changed_files=[_CHANGED],
    )
    return format_lint_results_for_prompt(results=reloaded), count_lint_results(
        reloaded,
    )


@pytest.mark.parametrize(
    ("entry", "action", "problem"),
    [
        pytest.param(
            {
                "tool": "tsc",
                "success": False,
                "status": "failed",
                "issues": [],
                "issues_count": 0,
                "remaining": 1,
            },
            None,
            "carries partial fix accounting",
            id="remaining-alone-on-a-failed-entry",
        ),
        pytest.param(
            {
                "tool": "tsc",
                "success": False,
                "status": "failed",
                "issues": [],
                "issues_count": 0,
                "remaining": 1,
                "fixed": 0,
                "net_resolved": 0,
            },
            None,
            "has fix counts (remaining 1, fixed 0) that do not fit its 0 listed",
            id="remaining-beyond-the-listed-findings",
        ),
        pytest.param(
            {
                "tool": "ruff",
                "success": False,
                "status": "failed",
                "issues": [_wire(_issue(_CHANGED, "a"))],
                "remaining": 10**100,
                "fixed": 0,
                "net_resolved": 0,
            },
            None,
            "has fix counts (remaining 1000",
            id="astronomical-remaining",
        ),
        pytest.param(
            {
                "tool": "ruff",
                "success": True,
                "status": "ok",
                "issues": [_wire(_issue(_CHANGED, "a"))],
                "remaining": 0,
                "fixed": 1,
                "net_resolved": 1,
            },
            "check",
            "carries fix accounting (remaining, fixed, net_resolved) in a check",
            id="fix-accounting-in-a-check-document",
        ),
        pytest.param(
            {
                "tool": "ruff",
                "success": False,
                "status": "failed",
                "issues": [_wire(_issue(_CHANGED, "a"))],
                "remaining": 1,
                "fixed": 1,
                "net_resolved": 0,
            },
            None,
            "has 'fixed' 1 but 'net_resolved' 0",
            id="fixed-is-not-net-resolved",
        ),
        pytest.param(
            {"tool": "ruff", "success": True, "status": "ok"},
            "fix",
            "has no fix accounting in a fix report",
            id="no-accounting-in-a-fix-document",
        ),
    ],
)
def test_remaining_that_breaks_the_rule_rejects_the_report(
    tmp_path: Path,
    entry: dict[str, Any],
    action: str | None,
    problem: str,
) -> None:
    """An unvalidated ``remaining`` can never stand in for a verdict.

    Args:
        tmp_path: Pytest temporary directory.
        entry: A report entry whose fix accounting breaks the rule.
        action: The document's ``action``.
        problem: What the rejection must name.
    """
    with pytest.raises(LintReportError, match="results\\[0\\]") as caught:
        _digest(tmp_path, [entry], action=action)

    assert_that(str(caught.value)).contains(problem)


def test_unknown_document_action_rejects_the_report(tmp_path: Path) -> None:
    """Only the actions lintro writes are accepted.

    Args:
        tmp_path: Pytest temporary directory.
    """
    with pytest.raises(LintReportError, match="unknown 'action' 'FIX'"):
        _digest(tmp_path, [{"tool": "ruff", "success": True}], action="FIX")


def test_some_remaining_note_is_dropped_when_no_listed_finding_is_reviewed(
    tmp_path: Path,
) -> None:
    """With every listed finding outside the diff, none remains in it.

    Args:
        tmp_path: Pytest temporary directory.
    """
    outside: dict[str, Any] = {
        "tool": "ruff",
        "success": False,
        "status": "failed",
        "issues": [_wire(_issue(_OTHER, "a", 1)), _wire(_issue(_OTHER, "b", 2))],
        "remaining": 1,
        "fixed": 1,
        "net_resolved": 1,
    }
    inside: dict[str, Any] = {
        **outside,
        "tool": "black",
        "issues": [_wire(_issue(_CHANGED, "a")), _wire(_issue(_OTHER, "b"))],
    }

    digest, counts = _digest(tmp_path, [outside, inside], action="fix")

    assert_that(digest).does_not_contain("Tool: ruff")
    assert_that(digest).contains("Tool: black | fix report: 1 issue(s) remain")
    assert_that((counts.ran, counts.incomplete, counts.issues)).is_equal_to(
        (2, 0, 1),
    )


def test_fix_row_without_measured_counts_says_so(tmp_path: Path) -> None:
    """Counts the writer defaulted to 0 beside findings are named as unmeasured.

    Args:
        tmp_path: Pytest temporary directory.
    """
    unmeasured = ToolResult(
        name="ruff",
        success=False,
        issues=[_issue(_CHANGED, "a", 1), _issue(_CHANGED, "b", 2)],
        issues_count=2,
        capability=Cap.FIX,
    )
    entry = serialize_tool_result(unmeasured, action=Action.FIX)
    assert_that((entry["remaining"], entry["fixed"])).is_equal_to((0, 0))

    digest, counts = _digest(tmp_path, [entry], action="fix")

    assert_that(digest).contains(
        "Tool: ruff | did not run / incomplete: report entry for ruff is a fix "
        "report with no measured counts for its 2 listed issue(s)",
    )
    assert_that((counts.ran, counts.incomplete)).is_equal_to((0, 1))


def _writer_entries() -> list[tuple[str, dict[str, Any]]]:
    """Serialise one result of every shape the writer produces, in both modes.

    Returns:
        ``(action, entry)`` pairs: check-mode shapes and fix-mode shapes.
    """
    a, b = _issue(_CHANGED, "a", 1), _issue(_CHANGED, "b", 2)
    check = [
        ToolResult(name="t", success=False, execution_failed=True, output="refused"),
        ToolResult(name="t", success=False, timed_out=True),
        ToolResult(name="t", success=False, issues=[a], parse_failures_count=1),
        ToolResult(
            name="t",
            success=False,
            issues=[a],
            residual_unknown=True,
            residual_unknown_reason="crashed",
        ),
        ToolResult(name="t", success=False, output="failed silently"),
        ToolResult(name="t", success=False, issues=[a, b], issues_count=2),
        ToolResult(name="t", success=True),
        ToolResult(name="t", success=True, skipped=True, skip_reason="off"),
        ToolResult.from_outcome(
            ToolOutcome(execution=Unavailable(reason="missing", strict=True)),
            name="t",
        ),
    ]
    fix = [
        ToolResult(
            name="t",
            success=True,
            initial_issues=[a, b],
            initial_issues_count=2,
            fixed_issues_count=2,
            remaining_issues_count=0,
            capability=Cap.FIX,
        ),
        ToolResult(
            name="t",
            success=False,
            issues=[b],
            issues_count=1,
            initial_issues=[a, b],
            initial_issues_count=2,
            fixed_issues_count=1,
            remaining_issues_count=1,
            capability=Cap.FIX,
        ),
        ToolResult(
            name="t",
            success=False,
            output="boom",
            execution_failed=True,
            residual_unknown=True,
            residual_unknown_reason="tool crashed",
            capability=Cap.FIX,
        ),
        ToolResult.from_outcome(
            ToolOutcome(
                execution=Checked(success=False),
                accounting=FixUnmeasured(reason="crashed", initial=2),
                issues_count=2,
            ),
            name="t",
            issues=[a, b],
            capability=Cap.FIX,
        ),
    ]
    return [
        *(("check", serialize_tool_result(r, action=Action.CHECK)) for r in check),
        *(("fix", serialize_tool_result(r, action=Action.FIX)) for r in fix),
    ]


#: Values tried for ``remaining``, and for ``fixed`` / ``net_resolved``.
_REMAINING: tuple[object, ...] = (0, 1, 2, 3, 10**100, -1, None, "1", True)
_FIXED: tuple[object, ...] = (0, 1, 2, -1, None)


def _alterations(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the entry with ``remaining`` added or altered every way tried.

    Args:
        entry: A writer-produced entry.

    Returns:
        Variants: ``remaining`` alone, and with every ``fixed`` value, and
        with ``fixed`` and ``net_resolved`` disagreeing.
    """
    variants = [{**entry, "remaining": value} for value in _REMAINING]
    for remaining, fixed in itertools.product(_REMAINING, _FIXED):
        variants.append(
            {**entry, "remaining": remaining, "fixed": fixed, "net_resolved": fixed},
        )
        variants.append(
            {**entry, "remaining": remaining, "fixed": fixed, "net_resolved": 7},
        )
    return variants


def test_altering_remaining_never_turns_an_unfinished_entry_into_ran(
    tmp_path: Path,
) -> None:
    """No ``remaining`` makes an incomplete writer shape count as ran.

    Every check- and fix-mode shape the writer produces is read back
    unaltered, then with ``remaining`` (and its companion counts) added or
    altered, in documents that record no action, ``fix`` or ``check``. Each
    variant is either rejected or stays incomplete if the original was.

    Args:
        tmp_path: Pytest temporary directory.
    """
    checked = 0
    for mode, entry in _writer_entries():
        _digest_text, baseline = _digest(tmp_path, [entry], action=mode)
        for variant, action in itertools.product(
            _alterations(entry),
            (None, "fix", "check"),
        ):
            try:
                _text, counts = _digest(tmp_path, [variant], action=action)
            except LintReportError:
                continue
            checked += 1
            if baseline.incomplete:
                assert_that(
                    (counts.ran, counts.incomplete),
                    description=f"{mode} entry {entry} as {variant} ({action})",
                ).is_equal_to((0, 1))

    assert_that(checked).is_greater_than(0)
