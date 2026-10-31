"""Tests for the severity baseline the count delta compares against."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.models.core.severity_counts import SeverityCounts
from lintro.models.core.tool_result import ToolResult
from lintro.utils.severity_baseline import (
    SEVERITY_BASELINE_FILENAME,
    read_severity_baseline,
    resolve_log_root,
    severity_baseline_scope,
    write_severity_baseline,
)


class _Manager:
    """Output-manager double exposing one ``base_dir`` value."""

    def __init__(self, base_dir: object) -> None:
        """Store the value this double reports as its log root.

        Args:
            base_dir: Whatever the double should expose as ``base_dir``.
        """
        self.base_dir = base_dir


def test_baseline_round_trips(tmp_path: Path) -> None:
    """Counts written for one run are read back by the next.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    counts = SeverityCounts(errors=3, warnings=1, info=7)

    write_severity_baseline(tmp_path, counts, "scope-a")

    assert_that(read_severity_baseline(tmp_path, "scope-a")).is_equal_to(counts)


def test_baseline_is_written_at_the_log_root_as_json(tmp_path: Path) -> None:
    """The file sits beside the run directories, and holds readable JSON.

    Placement matters because pruning only ever removes ``run-*`` directories;
    the format matters because anything else reading the file (a CI step, a
    person) must not need lintro to parse it.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    write_severity_baseline(tmp_path, SeverityCounts(errors=1, warnings=2), "scope-a")

    path = tmp_path / SEVERITY_BASELINE_FILENAME
    assert_that(path.is_file()).is_true()
    assert_that(json.loads(path.read_text(encoding="utf-8"))).is_equal_to(
        {"error": 1, "warning": 2, "info": 0, "total": 3, "scope": "scope-a"},
    )


def test_missing_baseline_reads_as_none(tmp_path: Path) -> None:
    """A first run in a workspace has nothing to compare against.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    assert_that(read_severity_baseline(tmp_path / "absent", "scope-a")).is_none()


def test_unparseable_baseline_reads_as_none(tmp_path: Path) -> None:
    """A corrupt baseline costs the delta line, it does not fail the run.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    (tmp_path / SEVERITY_BASELINE_FILENAME).write_text("{not json", encoding="utf-8")

    assert_that(read_severity_baseline(tmp_path, "scope-a")).is_none()


def test_non_mapping_baseline_reads_as_none(tmp_path: Path) -> None:
    """Valid JSON that is not an object is rejected rather than coerced.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    (tmp_path / SEVERITY_BASELINE_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")

    assert_that(read_severity_baseline(tmp_path, "scope-a")).is_none()


def test_write_creates_the_log_directory(tmp_path: Path) -> None:
    """Writing into a not-yet-created log directory works.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "nested" / ".lintro"

    write_severity_baseline(target, SeverityCounts(warnings=2), "scope-a")

    assert_that(read_severity_baseline(target, "scope-a")).is_equal_to(
        SeverityCounts(warnings=2),
    )


def test_write_failure_is_swallowed(tmp_path: Path) -> None:
    """An unwritable location must not raise out of a lint run.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")

    write_severity_baseline(blocker, SeverityCounts(errors=1), "scope-a")

    assert_that(read_severity_baseline(blocker, "scope-a")).is_none()


def test_baseline_scope_mismatch_neither_compares_nor_overwrites(
    tmp_path: Path,
) -> None:
    """A different population leaves the original baseline byte-for-byte intact.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    counts = SeverityCounts(errors=9, warnings=2)
    write_severity_baseline(tmp_path, counts, "full-run")
    path = tmp_path / SEVERITY_BASELINE_FILENAME
    original = path.read_bytes()

    assert_that(read_severity_baseline(tmp_path, "scoped-run")).is_none()
    write_severity_baseline(tmp_path, SeverityCounts(), "scoped-run")

    assert_that(path.read_bytes()).is_equal_to(original)
    assert_that(read_severity_baseline(tmp_path, "full-run")).is_equal_to(counts)


def test_matching_scope_updates_the_baseline(tmp_path: Path) -> None:
    """Consecutive measurements of the same population replace the old counts.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    write_severity_baseline(tmp_path, SeverityCounts(errors=4), "scope-a")

    write_severity_baseline(tmp_path, SeverityCounts(warnings=2), "scope-a")

    assert_that(read_severity_baseline(tmp_path, "scope-a")).is_equal_to(
        SeverityCounts(warnings=2),
    )


def test_legacy_baseline_has_no_comparable_scope(tmp_path: Path) -> None:
    """Legacy counts cannot be attributed to the current run's population.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    path = tmp_path / SEVERITY_BASELINE_FILENAME
    original = json.dumps(SeverityCounts(errors=6).to_dict())
    path.write_text(original, encoding="utf-8")

    assert_that(read_severity_baseline(tmp_path, "scope-a")).is_none()
    write_severity_baseline(tmp_path, SeverityCounts(), "scope-a")

    assert_that(path.read_text(encoding="utf-8")).is_equal_to(original)


def test_scope_normalizes_paths_and_executed_tool_sets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordering, duplicate arguments and skipped tools do not alter scope.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest fixture for changing the working directory.
    """
    monkeypatch.chdir(tmp_path)
    ruff = ToolResult(name="ruff", success=True)
    pylint = ToolResult(name="pylint", success=False, issues_count=2)
    expected = severity_baseline_scope(paths=["a", "b"], tool_results=[ruff, pylint])

    actual = severity_baseline_scope(
        paths=[str(tmp_path / "b"), "./a", "a/../b"],
        tool_results=[
            pylint,
            ruff,
            ruff,
            ToolResult(name="hadolint", skipped=True, skip_reason="not installed"),
        ],
    )

    assert_that(actual).is_equal_to(expected)


@pytest.mark.parametrize(
    ("paths", "tool_names"),
    [(["a.py"], ["ruff", "pylint"]), (["."], ["ruff"])],
    ids=["paths", "tools"],
)
def test_scope_changes_with_paths_or_executed_tools(
    paths: list[str],
    tool_names: list[str],
) -> None:
    """Both path selection and tool selection identify the measured population.

    Args:
        paths: Paths for the scoped run.
        tool_names: Executed tool names for the scoped run.
    """
    full = severity_baseline_scope(
        paths=["."],
        tool_results=[ToolResult(name=name) for name in ("ruff", "pylint")],
    )
    scoped = severity_baseline_scope(
        paths=paths,
        tool_results=[ToolResult(name=name) for name in tool_names],
    )

    assert_that(scoped).is_not_equal_to(full)


def test_resolve_log_root_accepts_a_path(tmp_path: Path) -> None:
    """A real path is returned unchanged.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    assert_that(resolve_log_root(_Manager(tmp_path))).is_equal_to(tmp_path)


def test_resolve_log_root_accepts_a_string(tmp_path: Path) -> None:
    """A string log root is normalised to a ``Path``.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    assert_that(resolve_log_root(_Manager(str(tmp_path)))).is_equal_to(tmp_path)


@pytest.mark.parametrize(
    "base_dir",
    [None, object(), 42],
    ids=["none", "not-a-path", "int"],
)
def test_resolve_log_root_rejects_anything_else(base_dir: object) -> None:
    """A manager double or half-built manager yields no log root.

    The baseline is then skipped rather than raising mid-run.

    Args:
        base_dir: Value the output-manager double exposes.
    """
    assert_that(resolve_log_root(_Manager(base_dir))).is_none()


def test_resolve_log_root_handles_a_manager_without_base_dir() -> None:
    """An object with no ``base_dir`` at all is handled, not probed blindly."""
    assert_that(resolve_log_root(object())).is_none()
