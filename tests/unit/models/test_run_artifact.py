"""Tests for the :class:`RunArtifact` value produced by the execute phase."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.utils.health_score import health_score_for_results


def test_default_artifact_is_an_empty_successful_run() -> None:
    """A default-constructed artifact describes a clean, empty run."""
    artifact = RunArtifact()

    assert_that(artifact.tool_results).is_empty()
    assert_that(artifact.action).is_equal_to(Action.CHECK)
    assert_that(artifact.workspace_root).is_equal_to(Path.cwd())
    assert_that(artifact.health).is_none()
    assert_that(artifact.total_issues).is_equal_to(0)
    assert_that(artifact.total_fixed).is_equal_to(0)
    assert_that(artifact.total_remaining).is_equal_to(0)
    assert_that(artifact.exit_code).is_equal_to(0)
    assert_that(artifact.dry_run_preview).is_false()
    assert_that(artifact.early_exit).is_false()
    assert_that(artifact.success).is_true()
    assert_that(artifact.health_score).is_equal_to(0)


def test_artifact_carries_every_field_of_a_real_run(tmp_path: Path) -> None:
    """A fully populated artifact round-trips each field it was given."""
    results = [ToolResult(name="ruff", success=False, issues_count=3, issues=[])]
    health = health_score_for_results(results)

    artifact = RunArtifact(
        tool_results=results,
        action=Action.CHECK,
        workspace_root=tmp_path,
        health=health,
        total_issues=3,
        total_fixed=0,
        total_remaining=3,
        exit_code=1,
    )

    assert_that(artifact.tool_results).is_length(1)
    assert_that(artifact.tool_results[0].name).is_equal_to("ruff")
    assert_that(artifact.action).is_equal_to(Action.CHECK)
    assert_that(artifact.workspace_root).is_equal_to(tmp_path)
    assert_that(artifact.total_issues).is_equal_to(3)
    assert_that(artifact.total_remaining).is_equal_to(3)
    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(artifact.success).is_false()
    assert_that(artifact.health_score).is_equal_to(health.score)


def test_early_exit_artifact_reports_its_failure() -> None:
    """An early-exit artifact carries the exit code and no results."""
    artifact = RunArtifact(action=Action.CHECK, exit_code=1, early_exit=True)

    assert_that(artifact.early_exit).is_true()
    assert_that(artifact.tool_results).is_empty()
    assert_that(artifact.success).is_false()
    # An un-scored run must not masquerade as a perfect one.
    assert_that(artifact.health_score).is_equal_to(0)
