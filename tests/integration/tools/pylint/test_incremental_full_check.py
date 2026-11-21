"""A full pylint check removes a clean entry it has just seen fail (#3578).

The run goes through :func:`finish_incremental_run`, the step every executor
runs after a check, with the real ``pylint`` binary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.pylint.pylint_issue import PylintIssue
from lintro.tools import tool_manager
from lintro.utils.incremental_record import finish_incremental_run
from tests.integration._tools import require_tool

pytestmark = require_tool("pylint")


def _check(root: Path, *, incremental: bool) -> tuple[ToolResult, list[str]]:
    """Run one pylint check the way an executor does.

    Args:
        root: Project to check.
        incremental: Whether this is an ``--incremental`` run.

    Returns:
        The result, and the names of the files the run was handed.
    """
    tool = tool_manager.get_tool("pylint").copy_for_execution()
    tool.set_options(incremental=incremental, disable="all", enable="E0611")
    result = tool.check([str(root)], {})
    handed = sorted(
        Path(path).name
        for check in tool.incremental_pending
        for path in check.snapshot.fingerprints
    )
    return finish_incremental_run(tool, result, record=True), handed


def test_a_full_check_invalidates_the_clean_entry_of_a_file_it_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sol's sequence: clean, change only ``b.py``, full fails, incremental fails.

    Args:
        tmp_path: Project and cache directory.
        monkeypatch: Points the incremental cache at ``tmp_path``.
    """
    monkeypatch.setattr("lintro.utils.file_cache.CACHE_DIR", tmp_path / "cache")
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("from b import VALUE\n\nprint(VALUE)\n")
    (root / "b.py").write_text("VALUE = 1\n")

    first, _ = _check(root, incremental=True)
    (root / "b.py").write_text("OTHER = 2\n")
    full, _ = _check(root, incremental=False)
    last, handed = _check(root, incremental=True)

    assert_that(first.success).is_true()
    assert_that(full.success).is_false()
    codes = [issue.code for issue in full.issues if isinstance(issue, PylintIssue)]
    assert_that(codes).contains("E0611")
    assert_that(handed).contains("a.py")
    assert_that(last.success).is_false()
    assert_that([Path(issue.file).name for issue in last.issues]).contains("a.py")
