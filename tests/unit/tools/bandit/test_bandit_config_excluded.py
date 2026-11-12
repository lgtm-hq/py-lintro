"""Files Bandit's own configuration excludes are never claimed (#3388).

Bandit 1.9 given a file that ``exclude_dirs`` (or ``--exclude``) covers exits
0 with ``errors: []``, no findings, and a ``metrics`` object holding only
``_totals``. Every file it does scan, even an empty one, gets a metrics entry
keyed by the path as given. A run whose metrics name none of the requested
files inspected nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import (
    CompletedProcess,
)  # nosec B404 - canned process results only; nothing is executed
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.bandit.definition import BanditPlugin

TOTALS = {"loc": 0, "nosec": 0, "skipped_tests": 0}


def _check(files: list[Path], metrics: dict[str, object] | None) -> ToolResult:
    """Run the real plugin against a canned clean Bandit report.

    Args:
        files: Python files to scan; each is written before the run.
        metrics: The report's ``metrics`` object, or None to omit it.

    Returns:
        The plugin's ToolResult.
    """
    for file in files:
        file.write_text("x = 1\n")
    report: dict[str, object] = {"errors": [], "results": []}
    if metrics is not None:
        report["metrics"] = metrics
    completed = CompletedProcess(
        args=["bandit"],
        returncode=0,
        stdout=json.dumps(report),
        stderr="",
    )
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch(
            "lintro.tools.bandit.definition.subprocess.run",
            return_value=completed,
        ),
    ):
        return BanditPlugin().check([str(file) for file in files], {})


def test_every_file_excluded_is_a_no_files_run(tmp_path: Path) -> None:
    """Metrics naming no requested file: nothing inspected, reason named."""
    result = _check([tmp_path / "a.py", tmp_path / "b.py"], {"_totals": TOTALS})

    assert_that(result.success).is_true()
    assert_that(result.no_files).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.issues_count).is_zero()
    assert_that(result.output).contains("excludes every file given").contains(
        "none was inspected",
    )


@pytest.mark.parametrize("spelling", ["as-given", "unnormalised"])
def test_a_scanned_file_keeps_the_inspection_claim(
    tmp_path: Path,
    spelling: str,
) -> None:
    """One metrics entry for a requested file proves the run scanned files."""
    kept, excluded = tmp_path / "kept.py", tmp_path / "excluded.py"
    key = str(kept) if spelling == "as-given" else f"{tmp_path}/./kept.py"

    result = _check([kept, excluded], {key: TOTALS, "_totals": TOTALS})

    assert_that(result.success).is_true()
    assert_that(result.no_files).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.output).is_none()


def test_a_report_without_metrics_is_read_as_before(tmp_path: Path) -> None:
    """No ``metrics`` object gives no coverage evidence either way."""
    result = _check([tmp_path / "a.py"], None)

    assert_that(result.success).is_true()
    assert_that(result.no_files).is_false()
    assert_that(result.inspected_files).is_true()
