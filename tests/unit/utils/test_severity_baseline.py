"""Tests for the severity baseline the count delta compares against."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.models.core.severity_counts import SeverityCounts
from lintro.utils.severity_baseline import (
    SEVERITY_BASELINE_FILENAME,
    read_severity_baseline,
    write_severity_baseline,
)


def test_baseline_round_trips(tmp_path: Path) -> None:
    """Counts written for one run are read back by the next.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    counts = SeverityCounts(errors=3, warnings=1, info=7)

    write_severity_baseline(tmp_path, counts)

    assert_that(read_severity_baseline(tmp_path)).is_equal_to(counts)


def test_baseline_is_written_at_the_log_root(tmp_path: Path) -> None:
    """The file sits beside the run directories so pruning never removes it.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    write_severity_baseline(tmp_path, SeverityCounts(errors=1))

    assert_that((tmp_path / SEVERITY_BASELINE_FILENAME).is_file()).is_true()


def test_missing_baseline_reads_as_none(tmp_path: Path) -> None:
    """A first run in a workspace has nothing to compare against.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    assert_that(read_severity_baseline(tmp_path / "absent")).is_none()


def test_unparseable_baseline_reads_as_none(tmp_path: Path) -> None:
    """A corrupt baseline costs the delta line, it does not fail the run.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    (tmp_path / SEVERITY_BASELINE_FILENAME).write_text("{not json", encoding="utf-8")

    assert_that(read_severity_baseline(tmp_path)).is_none()


def test_non_mapping_baseline_reads_as_none(tmp_path: Path) -> None:
    """Valid JSON that is not an object is rejected rather than coerced.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    (tmp_path / SEVERITY_BASELINE_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")

    assert_that(read_severity_baseline(tmp_path)).is_none()


def test_write_creates_the_log_directory(tmp_path: Path) -> None:
    """Writing into a not-yet-created log directory works.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "nested" / ".lintro"

    write_severity_baseline(target, SeverityCounts(warnings=2))

    assert_that(read_severity_baseline(target)).is_equal_to(SeverityCounts(warnings=2))


def test_write_failure_is_swallowed(tmp_path: Path) -> None:
    """An unwritable location must not raise out of a lint run.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")

    write_severity_baseline(blocker, SeverityCounts(errors=1))

    assert_that(read_severity_baseline(blocker)).is_none()
