"""Cross-format consistency for rule-code rendering (#1419).

The same issue must expose the same non-empty rule code in grid stdout,
CSV artifacts, and JSON ``issues[].code``. Tools that store the identifier
under an alias (e.g. yamllint ``rule``) previously diverged across formats.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.output_format import OutputFormat
from lintro.enums.severity_level import SeverityLevel
from lintro.formatters.formatter import format_issues
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.parsers.yamllint.yamllint_issue import YamllintIssue
from lintro.utils.json_output import serialize_issue
from lintro.utils.output import OutputManager
from lintro.utils.output.file_writer import write_output_file


def _yamllint_issue() -> YamllintIssue:
    """Return a yamllint violation fixture (code stored as ``rule``)."""
    return YamllintIssue(
        file="bad.yml",
        line=1,
        column=4,
        message="missing space after colon",
        level=SeverityLevel.WARNING,
        rule="colons",
    )


def _ruff_issue() -> RuffIssue:
    """Return a ruff violation fixture (code stored as ``code``)."""
    return RuffIssue(
        file="src/main.py",
        line=10,
        column=1,
        message="`os` imported but unused",
        code="F401",
    )


@pytest.mark.parametrize(
    ("tool_name", "issue", "expected_code"),
    [
        ("yamllint", _yamllint_issue(), "colons"),
        ("ruff", _ruff_issue(), "F401"),
    ],
    ids=["yamllint", "ruff"],
)
def test_rule_code_consistent_across_grid_csv_json(
    tmp_path: Path,
    tool_name: str,
    issue: YamllintIssue | RuffIssue,
    expected_code: str,
) -> None:
    """Grid, CSV, and JSON all emit the same non-empty rule code.

    Args:
        tmp_path: Temporary directory for CSV artifacts.
        tool_name: Tool name for ToolResult / CSV rows.
        issue: Issue fixture under test.
        expected_code: Canonical code that every format must emit.
    """
    assert_that(expected_code).is_not_empty()

    # Grid (stdout path uses to_display_row / DISPLAY_FIELD_MAP)
    grid = format_issues([issue], output_format=OutputFormat.GRID)
    assert_that(grid).contains(expected_code)

    # JSON issues[].code
    json_code = serialize_issue(issue)["code"]
    assert_that(json_code).is_equal_to(expected_code)

    result = ToolResult(
        name=tool_name,
        success=False,
        issues_count=1,
        output="",
        issues=[issue],
    )

    # User-specified CSV artifact (--output)
    csv_path = tmp_path / "report.csv"
    write_output_file(
        output_path=str(csv_path),
        output_format=OutputFormat.CSV,
        all_results=[result],
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert_that(rows).is_length(1)
    assert_that(rows[0]["code"]).is_equal_to(expected_code)

    # On-disk summary.csv from OutputManager
    om = OutputManager(base_dir=str(tmp_path / "runs"))
    om.write_reports_from_results([result])
    summary_path = om.get_run_dir() / "summary.csv"
    with summary_path.open(newline="", encoding="utf-8") as handle:
        summary_rows = list(csv.DictReader(handle))
    assert_that(summary_rows).is_length(1)
    assert_that(summary_rows[0]["code"]).is_equal_to(expected_code)

    # JSON file artifact also agrees
    json_path = tmp_path / "report.json"
    write_output_file(
        output_path=str(json_path),
        output_format=OutputFormat.JSON,
        all_results=[result],
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert_that(payload["results"][0]["issues"][0]["code"]).is_equal_to(expected_code)
