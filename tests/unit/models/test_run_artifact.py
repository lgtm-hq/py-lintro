"""Tests for the :class:`RunArtifact` value produced by the execute phase."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.severity_counts import SeverityCounts, SeverityDelta
from lintro.models.core.tool_result import ToolResult


def test_default_artifact_is_an_empty_successful_run() -> None:
    """A default-constructed artifact describes a clean, empty run."""
    artifact = RunArtifact()

    assert_that(artifact.tool_results).is_empty()
    assert_that(artifact.action).is_equal_to(Action.CHECK)
    assert_that(artifact.workspace_root).is_equal_to(Path.cwd())
    assert_that(artifact.severity_counts).is_equal_to(SeverityCounts())
    assert_that(artifact.previous_severity_counts).is_none()
    assert_that(artifact.severity_delta).is_none()
    assert_that(artifact.total_issues).is_equal_to(0)
    assert_that(artifact.total_fixed).is_equal_to(0)
    assert_that(artifact.total_remaining).is_equal_to(0)
    assert_that(artifact.exit_code).is_equal_to(0)
    assert_that(artifact.dry_run_preview).is_false()
    assert_that(artifact.early_exit).is_false()
    assert_that(artifact.success).is_true()


def test_artifact_carries_every_field_of_a_real_run(tmp_path: Path) -> None:
    """A fully populated artifact round-trips each field it was given."""
    results = [ToolResult(name="ruff", success=False, issues_count=3, issues=[])]

    artifact = RunArtifact(
        tool_results=results,
        action=Action.CHECK,
        workspace_root=tmp_path,
        severity_counts=SeverityCounts(errors=2, warnings=1),
        total_issues=3,
        total_fixed=0,
        total_remaining=3,
        exit_code=1,
    )

    assert_that(artifact.tool_results).is_length(1)
    assert_that(artifact.tool_results[0].name).is_equal_to("ruff")
    assert_that(artifact.action).is_equal_to(Action.CHECK)
    assert_that(artifact.workspace_root).is_equal_to(tmp_path)
    assert_that(artifact.severity_counts.total).is_equal_to(3)
    assert_that(artifact.total_issues).is_equal_to(3)
    assert_that(artifact.total_remaining).is_equal_to(3)
    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(artifact.success).is_false()


def test_severity_delta_is_current_minus_previous() -> None:
    """The delta subtracts the recorded baseline from this run's counts."""
    artifact = RunArtifact(
        severity_counts=SeverityCounts(errors=2, warnings=5, info=1),
        previous_severity_counts=SeverityCounts(errors=14, warnings=2, info=1),
    )

    assert_that(artifact.severity_delta).is_equal_to(
        SeverityDelta(errors=-12, warnings=3, info=0),
    )


def test_severity_delta_is_none_without_a_baseline() -> None:
    """A first run in a workspace has nothing to compare against."""
    artifact = RunArtifact(severity_counts=SeverityCounts(errors=1))

    assert_that(artifact.severity_delta).is_none()


def test_early_exit_artifact_reports_its_failure() -> None:
    """An early-exit artifact carries the exit code and no results."""
    artifact = RunArtifact(action=Action.CHECK, exit_code=1, early_exit=True)

    assert_that(artifact.early_exit).is_true()
    assert_that(artifact.tool_results).is_empty()
    assert_that(artifact.success).is_false()
    # A run that never executed must not report findings it did not make.
    assert_that(artifact.severity_counts.total).is_equal_to(0)
