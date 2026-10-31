"""Tests for the severity baseline the count delta compares against."""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from multiprocessing.synchronize import Event
    from typing import TextIO


def _write_paused_baseline(base_dir: Path, ready: Event, release: Event) -> None:
    """Pause a real writer after it has read its scope map.

    Args:
        base_dir: Shared log directory.
        ready: Signals that the snapshot has been read.
        release: Allows this writer to merge and persist its counts.
    """
    from lintro.utils import severity_baseline

    original_read = severity_baseline._read_baseline

    def paused_read(path: Path) -> dict[str, object] | None:
        """Hold the snapshot until the competing writer is ready."""
        scopes = original_read(path)
        ready.set()
        assert release.wait(15), "timed out waiting to release the first writer"
        return scopes

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(severity_baseline, "_read_baseline", paused_read)
        write_severity_baseline(base_dir, SeverityCounts(errors=2), "scope-a")


def _write_competing_baseline(base_dir: Path, settled: Event) -> None:
    """Signal when a second process has either written or hit a real lock.

    Args:
        base_dir: Shared log directory.
        settled: Signals completion or confirmed inter-process contention.
    """
    import fcntl

    original_flock = fcntl.flock

    def signal_contention(fd: int, operation: int) -> None:
        """Probe the real lock before blocking, without a timing assumption."""
        try:
            original_flock(fd, operation | fcntl.LOCK_NB)
        except BlockingIOError:
            settled.set()
            original_flock(fd, operation)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fcntl, "flock", signal_contention)
        write_severity_baseline(base_dir, SeverityCounts(warnings=3), "scope-b")
    settled.set()


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX lock contention probe")
@pytest.mark.parametrize("existing", [False, True], ids=["missing", "existing"])
def test_concurrent_writers_preserve_both_scopes(
    tmp_path: Path,
    existing: bool,
) -> None:
    """A paused read/merge/write cannot clobber another process's scope.

    Args:
        tmp_path: Pytest temporary directory fixture.
        existing: Whether a third scope already exists before either writer.
    """
    if existing:
        write_severity_baseline(tmp_path, SeverityCounts(info=4), "prior-scope")
    context = multiprocessing.get_context("spawn")
    ready, release, settled = (context.Event() for _ in range(3))
    first = context.Process(
        target=_write_paused_baseline,
        args=(tmp_path, ready, release),
    )
    second = context.Process(target=_write_competing_baseline, args=(tmp_path, settled))
    first.start()
    try:
        assert ready.wait(15), "first writer did not read the baseline"
        second.start()
        assert settled.wait(15), "second writer neither completed nor reached the lock"
    finally:
        release.set()
        for process in (first, second):
            if process.pid is not None:
                process.join(15)
                if process.is_alive():
                    process.terminate()
                    process.join(5)

    assert_that([first.exitcode, second.exitcode]).is_equal_to([0, 0])
    assert_that(read_severity_baseline(tmp_path, "scope-a")).is_equal_to(
        SeverityCounts(errors=2),
    )
    assert_that(read_severity_baseline(tmp_path, "scope-b")).is_equal_to(
        SeverityCounts(warnings=3),
    )
    if existing:
        assert_that(read_severity_baseline(tmp_path, "prior-scope")).is_equal_to(
            SeverityCounts(info=4),
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
        {"scopes": {"scope-a": {"error": 1, "warning": 2, "info": 0, "total": 3}}},
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


@pytest.mark.parametrize("outcome", ["success", "write-failure", "replace-failure"])
def test_atomic_write_preserves_the_published_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """Readers retain complete old JSON until a complete replacement is ready.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest fixture for injecting partial writes and failures.
        outcome: Whether publication succeeds or fails during write or replace.
    """
    previous = SeverityCounts(errors=5)
    current = SeverityCounts(warnings=2)
    write_severity_baseline(tmp_path, previous, "scope-a")
    path = tmp_path / SEVERITY_BASELINE_FILENAME
    original = path.read_bytes()

    def partial_dump(data: object, handle: TextIO, *, indent: int) -> None:
        """Inspect the public baseline while only partial new JSON exists."""
        content = json.dumps(data, indent=indent)
        handle.write(content[:3])
        handle.flush()
        assert_that(path.read_bytes()).is_equal_to(original)
        assert_that(read_severity_baseline(tmp_path, "scope-a")).is_equal_to(previous)
        assert_that(read_severity_baseline(tmp_path, "scope-b")).is_none()
        if outcome == "write-failure":
            raise OSError("simulated full disk during JSON write")
        handle.write(content[3:])

    def refuse_replace(source: Path, target: Path) -> Path:
        """Fail publication after the temporary JSON has been fully closed."""
        assert_that(json.loads(source.read_text(encoding="utf-8"))).is_equal_to(
            {"scopes": {"scope-a": previous.to_dict(), "scope-b": current.to_dict()}},
        )
        assert_that(target.read_bytes()).is_equal_to(original)
        raise PermissionError("simulated read-only baseline replacement")

    with monkeypatch.context() as patch:
        patch.setattr(json, "dump", partial_dump)
        if outcome == "replace-failure":
            patch.setattr(Path, "replace", refuse_replace)
        write_severity_baseline(tmp_path, current, "scope-b")

    assert_that(sorted(entry.name for entry in tmp_path.iterdir())).is_equal_to(
        [SEVERITY_BASELINE_FILENAME, SEVERITY_BASELINE_FILENAME + ".lock"],
    )
    if outcome != "success":
        assert_that(path.read_bytes()).is_equal_to(original)
        assert_that(read_severity_baseline(tmp_path, "scope-b")).is_none()
        # A subsequent real write proves cleanup also released the writer lock.
        write_severity_baseline(tmp_path, current, "scope-b")
    assert_that(read_severity_baseline(tmp_path, "scope-a")).is_equal_to(previous)
    assert_that(read_severity_baseline(tmp_path, "scope-b")).is_equal_to(current)


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="requires POSIX directory permissions without root privileges",
)
@pytest.mark.parametrize(
    "existing_lock",
    [False, True],
    ids=["new-lock", "existing-lock"],
)
def test_read_only_directory_preserves_baseline(
    tmp_path: Path,
    existing_lock: bool,
) -> None:
    """An unwritable log directory loses neither its old counts nor a lint run.

    Args:
        tmp_path: Pytest temporary directory fixture.
        existing_lock: Whether the persistent lock file already exists.
    """
    previous = SeverityCounts(errors=3)
    write_severity_baseline(tmp_path, previous, "scope-a")
    path = tmp_path / SEVERITY_BASELINE_FILENAME
    original = path.read_bytes()
    expected_files = [SEVERITY_BASELINE_FILENAME]
    if existing_lock:
        expected_files.append(SEVERITY_BASELINE_FILENAME + ".lock")
    else:
        (tmp_path / (SEVERITY_BASELINE_FILENAME + ".lock")).unlink()
    tmp_path.chmod(0o555)
    try:
        write_severity_baseline(tmp_path, SeverityCounts(), "scope-a")
        assert_that(path.read_bytes()).is_equal_to(original)
        assert_that(read_severity_baseline(tmp_path, "scope-a")).is_equal_to(previous)
        assert_that(sorted(entry.name for entry in tmp_path.iterdir())).is_equal_to(
            expected_files,
        )
    finally:
        tmp_path.chmod(0o755)


def test_baseline_scopes_advance_independently(
    tmp_path: Path,
) -> None:
    """An A/B/A sequence preserves each population's latest counts.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    counts = SeverityCounts(errors=9, warnings=2)
    write_severity_baseline(tmp_path, counts, "full-run")
    assert_that(read_severity_baseline(tmp_path, "scoped-run")).is_none()
    scoped_counts = SeverityCounts(warnings=3)
    write_severity_baseline(tmp_path, scoped_counts, "scoped-run")

    assert_that(read_severity_baseline(tmp_path, "full-run")).is_equal_to(counts)
    write_severity_baseline(tmp_path, SeverityCounts(errors=8), "full-run")
    assert_that(read_severity_baseline(tmp_path, "scoped-run")).is_equal_to(
        scoped_counts,
    )
    assert_that(read_severity_baseline(tmp_path, "full-run")).is_equal_to(
        SeverityCounts(errors=8),
    )


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
    """Unattributed legacy counts recover after one eligible measurement.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    path = tmp_path / SEVERITY_BASELINE_FILENAME
    original = json.dumps(SeverityCounts(errors=6).to_dict())
    path.write_text(original, encoding="utf-8")

    assert_that(read_severity_baseline(tmp_path, "scope-a")).is_none()
    write_severity_baseline(tmp_path, SeverityCounts(), "scope-a")

    assert_that(read_severity_baseline(tmp_path, "scope-a")).is_equal_to(
        SeverityCounts(),
    )


def test_single_scope_baseline_migrates_without_losing_its_counts(
    tmp_path: Path,
) -> None:
    """Adding a scope preserves counts written by the previous scoped format.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    counts = SeverityCounts(errors=6)
    (tmp_path / SEVERITY_BASELINE_FILENAME).write_text(
        json.dumps({**counts.to_dict(), "scope": "full-run"}),
        encoding="utf-8",
    )

    assert_that(read_severity_baseline(tmp_path, "full-run")).is_equal_to(counts)
    write_severity_baseline(tmp_path, SeverityCounts(warnings=1), "scoped-run")

    assert_that(read_severity_baseline(tmp_path, "full-run")).is_equal_to(counts)
    assert_that(read_severity_baseline(tmp_path, "scoped-run")).is_equal_to(
        SeverityCounts(warnings=1),
    )


@pytest.mark.parametrize("single_scope", [False, True], ids=["map", "single-scope"])
@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"error": -1},
        {"warning": "2"},
        {"info": 1.5},
        {"error": True},
        {"warning": None},
        {"total": 99},
        {"total": False},
    ],
    ids=[
        "null",
        "list",
        "missing-fields",
        "negative",
        "string",
        "float",
        "boolean",
        "null-count",
        "inconsistent-total",
        "boolean-total",
    ],
)
def test_invalid_counts_are_incomparable_and_replaced(
    tmp_path: Path,
    payload: dict[str, object] | list[object] | None,
    single_scope: bool,
) -> None:
    """Invalid counts cannot fabricate zero issues in either scoped format.

    Args:
        tmp_path: Pytest temporary directory fixture.
        payload: Invalid payload or overrides for otherwise valid counts.
        single_scope: Whether to exercise the older single-scope format.
    """
    entry = (
        {**SeverityCounts().to_dict(), **payload}
        if isinstance(payload, dict) and payload
        else payload
    )
    stored: dict[str, object] = {"scopes": {"scope-a": entry}}
    if single_scope:
        stored = {"scope": "scope-a"}
        if isinstance(entry, dict):
            stored.update(entry)
    (tmp_path / SEVERITY_BASELINE_FILENAME).write_text(
        json.dumps(stored),
        encoding="utf-8",
    )

    assert_that(read_severity_baseline(tmp_path, "scope-a")).is_none()
    replacement = SeverityCounts(warnings=2)
    write_severity_baseline(tmp_path, replacement, "scope-a")
    assert_that(read_severity_baseline(tmp_path, "scope-a")).is_equal_to(replacement)


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
