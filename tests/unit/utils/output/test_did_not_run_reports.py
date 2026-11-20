"""Every report says a tool did not run; none reads as a clean pass (#3556).

A refused security scanner fails the run by exit code, but the Markdown and
HTML reports said "No issues found.", CSV held a zero count, the plain report
"0 issues", and SARIF an empty ``results`` array. Each format now states that
the tool did not run and why, from the same statement (``verdict_gap``), and
SARIF records it on the invocation, not as a finding. The result here is the
real refusal result the version gate builds.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.output_format import OutputFormat
from lintro.models.core.tool_result import ToolResult
from lintro.utils.json_output import serialize_tool_result
from lintro.utils.output.file_writer import (
    render_csv_report,
    render_markdown_report,
    write_output_file,
)
from lintro.utils.output.manager import OutputManager
from lintro.utils.output.sarif import (
    execution_notifications,
    render_fixes_sarif,
    standard_issues_from_results,
)
from lintro.utils.tool_snapshots import below_minimum_result

_WHY = (
    "security scanner did not run: Version 2.8.0 is refused because it is "
    "known to give wrong results: skips packages (see https://example.invalid/kb)"
)

#: What every human-readable report must say about the refused scanner.
_STATEMENT = "osv_scanner did not run, so nothing was checked."


@pytest.fixture
def refused() -> ToolResult:
    """Return the strict result the gate builds for a refused scanner.

    Returns:
        A strict unavailable result for osv_scanner.
    """
    return below_minimum_result(
        name="osv_scanner",
        reason=_WHY,
        detail=f"security scanner did not run, so nothing was scanned. {_WHY}",
        strict=True,
    )


def _clean() -> ToolResult:
    """Return a complete, clean result for a tool that ran.

    Returns:
        A successful result with no issues.
    """
    return ToolResult(name="ruff", success=True, issues_count=0)


def test_markdown_report_says_the_scanner_did_not_run(refused: ToolResult) -> None:
    """Markdown names the scanner as not run, with the reason, never as clean.

    Args:
        refused: The refused scanner's result.
    """
    report = render_markdown_report([refused, _clean()], Action.CHECK)

    assert_that(report).contains(
        "| osv_scanner | did not run |",
        "### osv_scanner (did not run)",
        f"**{_STATEMENT}** Reason: {_WHY}",
        "| ruff | 0 |",
    )
    assert_that(report.count("No issues found.")).is_equal_to(1)


@pytest.mark.parametrize(
    ("output_format", "expected"),
    [
        (
            OutputFormat.HTML,
            (
                "<tr><td>osv_scanner</td><td>did not run</td></tr>",
                "<h3>osv_scanner (did not run)</h3>",
                f"<p><strong>{_STATEMENT}</strong> Reason: {_WHY}</p>",
            ),
        ),
        (
            OutputFormat.PLAIN,
            ("osv_scanner: did not run", f"{_STATEMENT} Reason: {_WHY}"),
        ),
    ],
    ids=["html", "plain"],
)
def test_report_file_says_the_scanner_did_not_run(
    tmp_path: Path,
    refused: ToolResult,
    output_format: OutputFormat,
    expected: tuple[str, ...],
) -> None:
    """The ``--output`` HTML and plain files state it too.

    Args:
        tmp_path: Directory for the report file.
        refused: The refused scanner's result.
        output_format: Report format under test.
        expected: Lines the report must contain.
    """
    path = tmp_path / "report.out"

    write_output_file(
        output_path=str(path),
        output_format=output_format,
        all_results=[refused],
        action=Action.CHECK,
        total_issues=0,
        total_fixed=0,
    )

    text = path.read_text(encoding="utf-8")
    assert_that(text).contains(*expected)
    assert_that(text).does_not_contain("No issues found", "0 issues")


def test_csv_notes_say_the_scanner_did_not_run(refused: ToolResult) -> None:
    """The CSV row's notes cell carries the statement and the reason.

    Args:
        refused: The refused scanner's result.
    """
    rows = list(csv.DictReader(io.StringIO(render_csv_report([refused]))))

    assert_that([row["tool"] for row in rows]).is_equal_to(["osv_scanner"])
    assert_that(rows[0]["notes"]).is_equal_to(
        f"{_STATEMENT} Reason: {_WHY}",
    )


def test_json_carries_the_refusal_in_structured_fields(refused: ToolResult) -> None:
    """JSON marks the scanner unavailable and gives the reason in its own field.

    Args:
        refused: The refused scanner's result.
    """
    data = serialize_tool_result(refused, action=Action.CHECK)

    assert_that(data).contains_entry(
        {"status": "unavailable"},
        {"unavailable": True},
        {"unavailable_reason": _WHY},
        {"success": False},
    )


def test_sarif_records_the_refusal_on_the_invocation(refused: ToolResult) -> None:
    """SARIF has no synthetic finding, and a failed invocation saying why.

    Args:
        refused: The refused scanner's result.
    """
    results = [refused, _clean()]

    document = json.loads(
        render_fixes_sarif(
            standard_issues_from_results(results),
            notifications=execution_notifications(results),
        ),
    )

    run = document["runs"][0]
    assert_that(run["results"]).is_empty()
    assert_that(run["invocations"]).is_equal_to(
        [
            {
                "executionSuccessful": False,
                "toolExecutionNotifications": [
                    {
                        "level": "error",
                        "message": {
                            "text": f"{_STATEMENT} Reason: {_WHY}",
                        },
                        "properties": {"tool": "osv_scanner"},
                    },
                ],
            },
        ],
    )


def test_sarif_of_a_complete_run_has_no_invocation_failure() -> None:
    """When every tool completed, SARIF stays as it was: no invocation block."""
    results = [_clean()]

    document = json.loads(
        render_fixes_sarif(
            standard_issues_from_results(results),
            notifications=execution_notifications(results),
        ),
    )

    assert_that(document["runs"][0]).does_not_contain_key("invocations")


def test_run_directory_reports_say_the_scanner_did_not_run(
    tmp_path: Path,
    refused: ToolResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reports written under ``.lintro/run-*`` state it as well.

    Args:
        tmp_path: Base directory for the run folder.
        refused: The refused scanner's result.
        monkeypatch: Clears the log-directory override.
    """
    monkeypatch.delenv("LINTRO_LOG_DIR", raising=False)
    manager = OutputManager(base_dir=str(tmp_path))

    manager.write_reports_from_results([refused])

    markdown = (manager.run_dir / "report.md").read_text(encoding="utf-8")
    html = (manager.run_dir / "report.html").read_text(encoding="utf-8")
    summary = (manager.run_dir / "summary.csv").read_text(encoding="utf-8")
    assert_that(markdown).contains("| osv_scanner | did not run |", f"**{_STATEMENT}**")
    assert_that(html).contains("<h3>osv_scanner (did not run)</h3>", _STATEMENT)
    assert_that(summary).contains(_STATEMENT)
    assert_that(markdown + html).does_not_contain("No issues found")
