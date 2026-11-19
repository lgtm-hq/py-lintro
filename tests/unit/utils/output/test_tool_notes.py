"""Notes about a complete result reach every output format (#3352).

osv-scanner notes the manifests whose indirect dependencies it could not
resolve. The note is kept in ``metadata["notes"]``, apart from the tool's
report, so a findings table, a summary row or a Markdown section never drops
it and the raw report still parses.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.osv_scanner import OsvScannerIssue
from lintro.utils.execution.run_renderer import _display_fix_result
from lintro.utils.json_output import serialize_tool_result
from lintro.utils.output.file_writer import render_markdown_report
from lintro.utils.output.manager import OutputManager
from lintro.utils.summary_tables import print_summary_table
from lintro.utils.tool_metadata import ToolNote, with_tool_notes

_NOTE = (
    "OSV-Scanner scanned the packages listed in these manifests but could "
    "not resolve their indirect dependencies, so those were not checked: "
    "requirements.txt: package System(7):zz-private: not found"
)

_REPORT = json.dumps({"results": [{"source": {"path": "requirements.txt"}}]})

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _result(*, findings: bool) -> ToolResult:
    """Build an osv-scanner result carrying one resolution note.

    Args:
        findings: Whether the scan found a vulnerability.

    Returns:
        A complete scan result, as the plugin builds it.
    """
    issues = (
        [
            OsvScannerIssue(
                file="requirements.txt",
                vuln_id="GHSA-68rp-wp8r-4726",
                package_name="flask",
                package_version="3.0.0",
                message="flask 3.0.0 is vulnerable",
            ),
        ]
        if findings
        else []
    )
    return ToolResult.from_outcome(
        ToolOutcome(
            execution=Checked(success=not findings, inspected_files=True),
            issues_count=len(issues),
        ),
        name="osv_scanner",
        output=_REPORT if findings else None,
        issues=issues,
        metadata=with_tool_notes(
            {},
            [ToolNote(summary="indirect dependencies not resolved", message=_NOTE)],
        ),
        parse_failures_count=0,
    )


def _console(result: ToolResult) -> str:
    """Render one result as the console shows it in grid format.

    Args:
        result: The result to render.

    Returns:
        Everything printed for it.
    """
    printed: list[str] = []

    def emit(text: str = "", **_: object) -> None:
        printed.append(text)

    _display_fix_result(
        result,
        output_format="grid",
        raw_output=False,
        console_output_func=emit,
        success_func=emit,
        action=Action.CHECK,
    )
    return "\n".join(printed)


@pytest.mark.parametrize("findings", [True, False], ids=["findings", "clean"])
def test_console_shows_the_note_with_or_without_findings(findings: bool) -> None:
    """The findings table replaces ``output``; the note is still printed.

    Args:
        findings: Whether the scan found a vulnerability.
    """
    text = _console(_result(findings=findings))

    assert_that(text).contains(f"Note: {_NOTE}")
    if findings:
        assert_that(text).contains("GHSA-68rp-wp8r-4726")


def test_summary_table_names_the_note() -> None:
    """The summary row's Notes column says what the note is about."""
    lines: list[str] = []

    def emit(text: str = "", **_: object) -> None:
        lines.append(text)

    print_summary_table(emit, Action.CHECK, [_result(findings=True)])

    rows = _ANSI.sub("", "\n".join(lines)).splitlines()
    row = next(line for line in rows if "osv" in line.lower())
    assert_that(row).contains("indirect dependencies not resolved")


@pytest.mark.parametrize("findings", [True, False], ids=["findings", "clean"])
def test_markdown_reports_show_the_note(findings: bool, tmp_path: Path) -> None:
    """Both Markdown renderers quote the note under the tool's section.

    Args:
        findings: Whether the scan found a vulnerability.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    result = _result(findings=findings)
    manager = OutputManager(base_dir=str(tmp_path))

    for report in (
        render_markdown_report([result], action=Action.CHECK),
        manager._render_markdown_tables(results=[result]),
    ):
        assert_that(report).contains(f"> **Note:** {_NOTE}")


def test_json_keeps_the_note_apart_from_a_parseable_report() -> None:
    """``metadata.notes`` carries the note; ``output`` is the raw report."""
    payload = serialize_tool_result(_result(findings=True), action=Action.CHECK)

    assert_that(payload["metadata"]["notes"]).is_equal_to(
        [{"summary": "indirect dependencies not resolved", "message": _NOTE}],
    )
    assert_that(json.loads(payload["output"])).contains_key("results")
