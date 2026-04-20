"""Unit tests for report generation via OutputManager."""

from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.utils.output import OutputManager


class DummyIssue:
    """Simple container for issue fields used in reports."""

    def __init__(self, file: str, line: int, code: str, message: str) -> None:
        """Initialize an issue container.

        Args:
            file: File path where the issue occurred.
            line: Line number of the issue.
            code: Issue code identifier.
            message: Human-readable message.
        """
        self.file = file
        self.line = line
        self.code = code
        self.message = message


class DummyResult:
    """Simple result container used to exercise report writing."""

    def __init__(
        self,
        name: str,
        issues_count: int,
        issues: list[DummyIssue] | None = None,
    ) -> None:
        """Initialize a result wrapper.

        Args:
            name: Tool name associated with the result.
            issues_count: Total number of issues.
            issues: Optional list of issue objects.
        """
        self.name = name
        self.issues_count = issues_count
        self.issues = issues or []


def test_output_manager_writes_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write multiple report formats and verify artifacts exist.

    Args:
        tmp_path: Temporary directory for placing report outputs.
        monkeypatch: Pytest monkeypatch to set output directory.
    """
    monkeypatch.setenv("LINTRO_LOG_DIR", str(tmp_path))
    om = OutputManager()
    issues = [DummyIssue(file="a.py", line=1, code="X", message="m")]
    results = [DummyResult(name="ruff", issues_count=1, issues=issues)]
    om.write_reports_from_results(results=results)  # type: ignore[arg-type]
    assert_that((om.run_dir / "report.md").exists()).is_true()
    assert_that((om.run_dir / "report.html").exists()).is_true()
    assert_that((om.run_dir / "summary.csv").exists()).is_true()


def test_output_manager_creates_unique_run_dirs_for_colliding_timestamps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated runs in the same instant should still get distinct directories."""

    class FixedDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz: datetime.tzinfo | None = None) -> FixedDateTime:
            return cls(2026, 4, 17, 14, 30, 0, 123456, tzinfo=tz)

    monkeypatch.setattr("lintro.utils.output.manager.datetime.datetime", FixedDateTime)
    monkeypatch.setattr("lintro.utils.output.manager.os.getpid", lambda: 4242)

    first = OutputManager(base_dir=str(tmp_path))
    second = OutputManager(base_dir=str(tmp_path))

    assert_that(first.run_dir).is_not_equal_to(second.run_dir)
    assert_that(first.run_dir.name).is_equal_to("run-20260417-143000-123456-4242-0000")
    assert_that(second.run_dir.name).is_equal_to("run-20260417-143000-123456-4242-0001")


def test_cleanup_old_runs_preserves_active_run_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup should not delete an older run directory owned by a live process."""
    monkeypatch.setenv("LINTRO_LOG_DIR", str(tmp_path))
    manager = OutputManager(keep_last=2)

    active_old_run = tmp_path / "run-20000101-000000-000000-9999"
    active_old_run.mkdir()
    (active_old_run / ".active").write_text(f"{os.getpid()}\n", encoding="utf-8")

    for suffix in (1, 2, 3):
        (tmp_path / f"run-20000101-00000{suffix}-000000-9999").mkdir()

    manager.cleanup_old_runs()

    assert_that(active_old_run.exists()).is_true()
    assert_that((tmp_path / "run-20000101-000001-000000-9999").exists()).is_false()
    assert_that((tmp_path / "run-20000101-000002-000000-9999").exists()).is_false()
    assert_that((tmp_path / "run-20000101-000003-000000-9999").exists()).is_true()


def test_create_run_dir_fallback_updates_base_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When primary base_dir is unwritable, fallback must redirect base_dir."""
    unwritable = tmp_path / "unwritable"
    fallback_base = tmp_path / "fallback-tmp"
    fallback_base.mkdir()

    monkeypatch.setenv("LINTRO_LOG_DIR", str(unwritable))
    monkeypatch.setattr(
        "lintro.utils.output.manager.tempfile.gettempdir",
        lambda: str(fallback_base.parent),
    )
    monkeypatch.setattr(
        "lintro.utils.output.manager.DEFAULT_TEMP_PREFIX",
        fallback_base.name,
    )

    real_create = OutputManager._create_unique_run_dir
    calls: list[Path] = []

    def tracked_create(self: OutputManager, base_dir: Path, timestamp: str) -> Path:
        calls.append(base_dir)
        if base_dir == unwritable:
            raise PermissionError("simulated unwritable base_dir")
        return real_create(self, base_dir, timestamp)

    monkeypatch.setattr(
        "lintro.utils.output.manager.OutputManager._create_unique_run_dir",
        tracked_create,
    )

    manager = OutputManager()

    assert_that(manager.base_dir).is_equal_to(fallback_base)
    assert_that(manager.run_dir.parent).is_equal_to(fallback_base)
    assert_that(calls[0]).is_equal_to(unwritable)


def test_cleanup_old_runs_removes_stale_pid_marker_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A marker pointing at a dead PID should not protect an old run dir."""
    monkeypatch.setenv("LINTRO_LOG_DIR", str(tmp_path))
    manager = OutputManager(keep_last=2)

    stale_run = tmp_path / "run-20000101-000000-000000-9999"
    stale_run.mkdir()
    stale_marker = stale_run / ".active"
    stale_marker.write_text("424242\n", encoding="utf-8")

    for suffix in (1, 2, 3):
        (tmp_path / f"run-20000101-00000{suffix}-000000-9999").mkdir()

    monkeypatch.setattr(
        "lintro.utils.output.manager.OutputManager._pid_is_active",
        lambda _self, _pid: False,
    )

    manager.cleanup_old_runs()

    assert_that(stale_run.exists()).is_false()
    assert_that(stale_marker.exists()).is_false()
