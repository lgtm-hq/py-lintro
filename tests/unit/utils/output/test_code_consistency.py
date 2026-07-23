"""Cross-format rule-code consistency tests (issue #1419).

The same issue must render the same non-empty rule code in grid stdout, the
CSV artifact, and the JSON ``results[].issues[].code`` payload, regardless of
whether the underlying issue dataclass stores the code as ``code`` or under a
tool-specific field name (e.g. yamllint's ``rule``).
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.output_format import OutputFormat
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.parsers.yamllint.yamllint_issue import YamllintIssue
from lintro.utils.json_output import serialize_tool_result
from lintro.utils.output.file_writer import format_tool_output, write_output_file


def _yamllint_case() -> tuple[str, list[BaseIssue], str]:
    issues: list[BaseIssue] = [
        YamllintIssue(
            file="a.yaml",
            line=2,
            column=4,
            message="too many",
            rule="colons",
        ),
    ]
    return "yamllint", issues, "colons"


def _ruff_case() -> tuple[str, list[BaseIssue], str]:
    issues: list[BaseIssue] = [
        RuffIssue(file="a.py", line=1, column=1, message="unused import", code="F401"),
    ]
    return "ruff", issues, "F401"


@pytest.mark.parametrize("case", [_yamllint_case(), _ruff_case()])
def test_code_is_consistent_across_grid_csv_json(
    case: tuple[str, list[BaseIssue], str],
    tmp_path: Path,
) -> None:
    """Grid, CSV, and JSON all render the same non-empty code for one issue."""
    tool_name, issues, expected_code = case
    result = ToolResult(
        name=tool_name,
        success=False,
        output="",
        issues_count=len(issues),
        issues=issues,
    )

    # Grid stdout
    grid_output = format_tool_output(
        tool_name=tool_name,
        output="",
        output_format="grid",
        issues=issues,
    )
    assert_that(grid_output).contains(expected_code)

    # JSON results[].issues[].code
    serialized = serialize_tool_result(result, action=Action.CHECK)
    json_codes = [issue["code"] for issue in serialized["issues"]]
    assert_that(json_codes).is_equal_to([expected_code])

    # CSV artifact
    csv_path = tmp_path / "summary.csv"
    write_output_file(
        output_path=str(csv_path),
        output_format=OutputFormat.CSV,
        all_results=[result],
        action=Action.CHECK,
        total_issues=len(issues),
        total_fixed=0,
    )
    reader = csv.DictReader(io.StringIO(csv_path.read_text(encoding="utf-8")))
    csv_codes = [row["code"] for row in reader]
    assert_that(csv_codes).is_equal_to([expected_code])
