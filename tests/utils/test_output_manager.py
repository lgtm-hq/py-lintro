"""Unit tests for `OutputManager` write helpers and report generation."""

import csv
import json
import shutil
import tempfile
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.parsers.base_issue import BaseIssue
from lintro.utils.output import OutputManager


@dataclass
class _ReportIssue(BaseIssue):
    """Minimal BaseIssue subclass for report tests.

    Attributes:
        code: Rule code, which may be ``None`` to simulate parsers that omit it.
    """

    code: str | None = None


@pytest.fixture
def temp_output_dir() -> Generator[str, None, None]:
    """Provide a temporary directory path and clean up afterwards.

    Yields:
        str: Path to a temporary directory for test outputs.
    """
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def make_tool_result(
    name: str,
    issues_count: int = 0,
    issues: list[Any] | None = None,
) -> SimpleNamespace:
    """Factory for tool-like result objects used in report tests.

    Args:
        name: Tool name.
        issues_count: Total issues count.
        issues: Optional list of issues.

    Returns:
        SimpleNamespace with name, issues_count, output, and issues.
    """
    return SimpleNamespace(
        name=name,
        issues_count=issues_count,
        output=f"Output for {name}",
        issues=issues or [],
    )


def make_issue(file: str, line: int, code: str, message: str) -> SimpleNamespace:
    """Factory for issue-like objects used in report tests.

    Args:
        file: File path.
        line: Line number.
        code: Issue code.
        message: Description.

    Returns:
        A BaseIssue subclass instance with file, line, code, and message.
    """
    return _ReportIssue(file=file, line=line, code=code, message=message)


def test_run_dir_creation(temp_output_dir: str) -> None:
    """Test that OutputManager creates a timestamped run directory.

    Args:
        temp_output_dir: Temporary directory fixture for test output.
    """
    om = OutputManager(base_dir=temp_output_dir, keep_last=2)
    assert_that(om.get_run_dir().exists()).is_true()
    assert_that(om.get_run_dir().parent).is_equal_to(Path(temp_output_dir))


def test_write_console_log(temp_output_dir: str) -> None:
    """Test writing console.log file.

    Args:
        temp_output_dir: Temporary directory fixture for test output.
    """
    om = OutputManager(base_dir=temp_output_dir)
    om.write_console_log("hello world")
    log_path = om.get_run_dir() / "console.log"
    assert_that(log_path.exists()).is_true()
    assert_that(log_path.read_text()).is_equal_to("hello world")


def test_write_json(temp_output_dir: str) -> None:
    """Test writing results.json file.

    Args:
        temp_output_dir: Temporary directory fixture for test output.
    """
    om = OutputManager(base_dir=temp_output_dir)
    data = {"foo": 1, "bar": [2, 3]}
    om.write_json(data)
    json_path = om.get_run_dir() / "results.json"
    assert_that(json_path.exists()).is_true()
    with open(json_path) as f:
        loaded = json.load(f)
    assert_that(loaded).is_equal_to(data)


def test_write_markdown(temp_output_dir: str) -> None:
    """Test writing report.md file.

    Args:
        temp_output_dir: Temporary directory fixture for test output.
    """
    om = OutputManager(base_dir=temp_output_dir)
    om.write_markdown("# Title\nBody")
    md_path = om.get_run_dir() / "report.md"
    assert_that(md_path.exists()).is_true()
    assert_that(md_path.read_text().startswith("# Title")).is_true()


def test_write_html(temp_output_dir: str) -> None:
    """Test writing report.html file.

    Args:
        temp_output_dir: Temporary directory fixture for test output.
    """
    om = OutputManager(base_dir=temp_output_dir)
    om.write_html("<h1>Title</h1>")
    html_path = om.get_run_dir() / "report.html"
    assert_that(html_path.exists()).is_true()
    assert_that(html_path.read_text()).contains("<h1>Title</h1>")


def test_write_csv(temp_output_dir: str) -> None:
    """Test writing summary.csv file.

    Args:
        temp_output_dir: Temporary directory fixture for test output.
    """
    om = OutputManager(base_dir=temp_output_dir)
    rows = [["a", "1"], ["b", "2"]]
    header = ["col1", "col2"]
    om.write_csv(rows, header)
    csv_path = om.get_run_dir() / "summary.csv"
    assert_that(csv_path.exists()).is_true()
    with open(csv_path) as f:
        reader = list(csv.reader(f))
    assert_that(reader[0]).is_equal_to(header)
    assert_that(reader[1]).is_equal_to(["a", "1"])
    assert_that(reader[2]).is_equal_to(["b", "2"])


def test_write_reports_from_results(temp_output_dir: str) -> None:
    """Test write_reports_from_results generates all report files with correct content.

    Args:
        temp_output_dir: Temporary directory fixture for test output.
    """
    om = OutputManager(base_dir=temp_output_dir)
    issues = [make_issue("foo.py", 1, "E001", "Test error")]
    results = [make_tool_result("tool1", 1, issues), make_tool_result("tool2", 0, [])]
    om.write_reports_from_results(results)  # type: ignore[arg-type]
    md = (om.get_run_dir() / "report.md").read_text()
    assert_that("tool1" in md and "foo.py" in md and ("E001" in md)).is_true()
    html = (om.get_run_dir() / "report.html").read_text()
    assert_that("tool1" in html and "foo.py" in html and ("E001" in html)).is_true()
    csv_path = om.get_run_dir() / "summary.csv"
    with open(csv_path) as f:
        reader = list(csv.reader(f))
    assert_that(reader[0][:2]).is_equal_to(["tool", "issues_count"])
    assert_that(any("tool1" in row for row in reader)).is_true()
    assert_that(any("foo.py" in row for row in reader)).is_true()


def test_write_reports_from_results_with_none_code(temp_output_dir: str) -> None:
    """write_reports_from_results handles issues where code is None.

    Ensures the consumer safety net (or "") prevents crashes when a tool
    parser produces issues with code=None.

    Args:
        temp_output_dir: Temporary directory fixture for test output.
    """
    om = OutputManager(base_dir=temp_output_dir)
    issues = [_ReportIssue(file="bar.py", line=5, code=None, message="Some warning")]
    results = [make_tool_result("tool1", 1, issues)]
    om.write_reports_from_results(results)  # type: ignore[arg-type]
    md = (om.get_run_dir() / "report.md").read_text()
    assert_that(md).contains("bar.py")
    assert_that(md).contains("Some warning")
    assert_that(md).does_not_contain("None")
    html_content = (om.get_run_dir() / "report.html").read_text()
    assert_that(html_content).contains("bar.py")
    assert_that(html_content).contains("Some warning")
    assert_that(html_content).does_not_contain("None")
    csv_path = om.get_run_dir() / "summary.csv"
    with open(csv_path) as f:
        reader = list(csv.reader(f))
    assert_that(any("bar.py" in row for row in reader)).is_true()
    # Ensure "None" string doesn't appear in CSV rows
    for row in reader:
        for cell in row:
            assert_that(cell).is_not_equal_to("None")


def test_permission_fallback_uses_temp_dir(temp_output_dir: str) -> None:
    """Verify PermissionError falls back to temp directory with warning.

    When OutputManager cannot write to the base directory, it should fall back
    to a temp directory and log a warning.

    Args:
        temp_output_dir: Temporary directory fixture for test output.
    """
    from unittest.mock import patch

    # Create a restricted directory path
    restricted_path = Path(temp_output_dir) / "restricted"

    with patch("lintro.utils.output.manager.logger") as mock_logger:
        # Patch mkdir to raise PermissionError on first call, succeed on second
        original_mkdir = Path.mkdir

        call_count = [0]

        def mock_mkdir(self: Path, *args: Any, **kwargs: Any) -> None:
            call_count[0] += 1
            if call_count[0] == 1:
                raise PermissionError("Permission denied")
            return original_mkdir(self, *args, **kwargs)

        with patch.object(Path, "mkdir", mock_mkdir):
            om = OutputManager(base_dir=str(restricted_path))

            # Should have fallen back to temp directory
            assert_that(str(om.run_dir).startswith(str(restricted_path))).is_false()
            assert_that(str(om.run_dir).startswith(tempfile.gettempdir())).is_true()

            # Warning should have been logged
            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert_that(warning_msg).contains("Cannot write to")
            assert_that(warning_msg).contains("permission denied")
            assert_that(warning_msg).contains("using fallback")
