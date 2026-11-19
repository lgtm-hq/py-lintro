"""Notes about a complete result reach every output format (#3352).

osv-scanner notes the manifests whose indirect dependencies it could not
resolve. The note is kept in ``metadata["notes"]``, apart from the tool's
report, so a findings table, a summary row or a Markdown section never drops
it and the raw report still parses.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.output_format import OutputFormat
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.osv_scanner import OsvScannerIssue
from lintro.utils.execution.run_renderer import _display_fix_result
from lintro.utils.json_output import serialize_tool_result
from lintro.utils.output.file_writer import (
    render_csv_report,
    render_markdown_report,
    write_output_file,
)
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


#: The columns saved CSV reports had before notes, in their original order.
_CSV_COLUMNS = ["tool", "issues_count", "file", "line", "code", "message", "doc_url"]


def _saved(tmp_path: Path, result: ToolResult, output_format: OutputFormat) -> str:
    """Write one result with ``--output`` and read the file back.

    Args:
        tmp_path: Directory for the report.
        result: The result to write.
        output_format: The report format.

    Returns:
        The saved report.
    """
    path = tmp_path / f"report.{output_format.value}"
    write_output_file(
        output_path=str(path),
        output_format=output_format,
        all_results=[result],
        action=Action.CHECK,
        total_issues=result.issues_count,
        total_fixed=0,
    )
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("findings", [True, False], ids=["findings", "clean"])
def test_saved_html_reports_qualify_the_result_with_the_note(
    findings: bool,
    tmp_path: Path,
) -> None:
    """Both HTML reports print the note under the tool, beside "No issues".

    Args:
        findings: Whether the scan found a vulnerability.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    result = _result(findings=findings)
    manager = OutputManager(base_dir=str(tmp_path / "runs"))
    manager._write_html_report(results=[result])

    for report in (
        _saved(tmp_path, result, OutputFormat.HTML),
        (manager.run_dir / "report.html").read_text(encoding="utf-8"),
    ):
        assert_that(report).contains(f"<p><strong>Note:</strong> {_NOTE}</p>")
        if not findings:
            assert_that(report).contains("<p>No issues found.</p>")


@pytest.mark.parametrize("findings", [True, False], ids=["findings", "clean"])
def test_saved_plain_report_shows_the_note(findings: bool, tmp_path: Path) -> None:
    """The plain-text report prints the note in the tool's section.

    Args:
        findings: Whether the scan found a vulnerability.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    report = _saved(tmp_path, _result(findings=findings), OutputFormat.PLAIN)

    section = report.split("osv_scanner:", 1)[1].split("Total Issues:", 1)[0]
    assert_that(section).contains(f"Note: {_NOTE}")


@pytest.mark.parametrize("findings", [True, False], ids=["findings", "clean"])
def test_csv_reports_add_a_notes_column_after_the_original_ones(
    findings: bool,
    tmp_path: Path,
) -> None:
    """``--output`` CSV and ``summary.csv`` keep their columns and add notes.

    Args:
        findings: Whether the scan found a vulnerability.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    result = _result(findings=findings)
    manager = OutputManager(base_dir=str(tmp_path / "runs"))
    manager._write_csv_summary(results=[result])
    reports = {
        "output": (_saved(tmp_path, result, OutputFormat.CSV), _CSV_COLUMNS),
        "summary": (
            (manager.run_dir / "summary.csv").read_text(encoding="utf-8"),
            _CSV_COLUMNS[:-1],
        ),
    }

    for name, (text, columns) in reports.items():
        rows = list(csv.DictReader(io.StringIO(text)))
        assert_that(list(rows[0])).described_as(name).is_equal_to(
            [*columns, "notes"],
        )
        assert_that([row["notes"] for row in rows]).described_as(name).is_equal_to(
            [_NOTE],
        )


def test_csv_notes_cell_is_empty_for_a_result_without_notes() -> None:
    """A tool that attached no note leaves the new column empty."""
    result = ToolResult.from_outcome(
        ToolOutcome(execution=Checked(success=True, inspected_files=True)),
        name="ruff",
        parse_failures_count=0,
    )

    rows = list(csv.DictReader(io.StringIO(render_csv_report([result]))))

    assert_that(rows[0]["notes"]).is_equal_to("")
    assert_that(rows[0]["tool"]).is_equal_to("ruff")
