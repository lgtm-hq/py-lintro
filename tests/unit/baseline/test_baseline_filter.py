"""Final accounting stays coherent after fingerprint filtering."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.utils.execution.baseline_filter import apply_baseline
from lintro.utils.fingerprint_baseline import snapshot
from lintro.utils.json_output import create_json_output
from lintro.utils.meaningful_run import baseline_is_eligible
from tests.unit.tools.subprocess_helpers import subprocess_result


def test_check_filters_findings_exit_and_serialized_counts_together(
    tmp_path: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
) -> None:
    """Reports and exit status count only unacknowledged occurrences."""
    baseline = snapshot([dirty_result], root=tmp_path)
    result = replace(
        dirty_result,
        issues=[finding, replace(finding, line=99)],
        issues_count=2,
        output="native raw text with acknowledged issues",
        formatted_output="cached",
    )
    artifact = RunArtifact(
        tool_results=[result],
        workspace_root=tmp_path,
        total_issues=2,
        exit_code=1,
    )
    apply_baseline(artifact, baseline)
    payload = create_json_output(
        "check",
        artifact.tool_results,
        artifact.total_issues,
        artifact.total_fixed,
        artifact.total_remaining,
        artifact.exit_code,
    )
    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(artifact.total_issues).is_equal_to(1)
    assert_that(payload["results"][0]["issues_count"]).is_equal_to(1)
    assert_that(payload["results"][0]["issues"]).is_length(1)
    assert_that(payload["summary"]["total_remaining"]).is_equal_to(1)
    assert_that(payload["summary"]["total_baselined"]).is_equal_to(1)
    assert_that(result.formatted_output).is_none()
    assert_that(result.output).is_equal_to("native raw text with acknowledged issues")
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=[result],
        ),
    ).is_false()


def test_all_acknowledged_lint_findings_pass_without_appearing_fixed(
    tmp_path: Path,
    dirty_result: ToolResult,
) -> None:
    """Acknowledgements are distinct from fixed or remaining issue counts."""
    baseline = snapshot([dirty_result], root=tmp_path)
    artifact = RunArtifact(
        tool_results=[dirty_result],
        workspace_root=tmp_path,
        total_issues=1,
        total_remaining=1,
        exit_code=1,
    )
    apply_baseline(artifact, baseline)
    assert_that(artifact.success).is_true()
    assert_that(artifact.total_issues).is_zero()
    assert_that(artifact.total_fixed).is_zero()
    assert_that(artifact.total_remaining).is_zero()
    assert_that(dirty_result.baselined_count).is_equal_to(1)


@pytest.mark.parametrize(
    "state",
    [
        {"inspected_files": None},
        {"parse_failures_count": 1},
        {"timed_out": True},
        {"residual_unknown": True, "residual_unknown_reason": "incomplete verify"},
    ],
)
def test_acknowledgements_cannot_hide_incomplete_inspection(
    tmp_path: Path,
    dirty_result: ToolResult,
    state: dict[str, Any],
) -> None:
    """Incomplete reports retain both their findings and failing exit status."""
    baseline = snapshot([dirty_result], root=tmp_path)
    result = replace(dirty_result, **state)
    artifact = RunArtifact(
        tool_results=[result],
        workspace_root=tmp_path,
        total_issues=1,
        exit_code=1,
    )
    apply_baseline(artifact, baseline)
    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(result.issues).is_length(1)
    assert_that(result.baselined_count).is_zero()


def test_tool_error_and_policy_failure_survive_acknowledged_peer(
    tmp_path: Path,
    dirty_result: ToolResult,
) -> None:
    """A failing peer or policy cannot be converted into a clean lint verdict."""
    baseline = snapshot([dirty_result], root=tmp_path)
    error = ToolResult(name="mypy", success=False, output="compiler crashed")
    artifact = RunArtifact(
        tool_results=[dirty_result, error],
        workspace_root=tmp_path,
        total_issues=1,
        exit_code=1,
        force_failure=True,
    )
    apply_baseline(artifact, baseline)
    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(error.output).is_equal_to("compiler crashed")
    assert_that(artifact.total_issues).is_zero()
    assert_that(error.success).is_false()


def test_verified_residual_and_detected_findings_are_filtered_independently(
    tmp_path: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
) -> None:
    """Baselining does not spend one shared budget across before/after lists."""
    baseline = snapshot([dirty_result], root=tmp_path)
    new = replace(finding, code="F999")
    result = replace(
        dirty_result,
        initial_issues=[finding, new],
        initial_issues_count=2,
        fixed_issues_count=0,
        remaining_issues_count=2,
        issues=[finding, new],
        issues_count=2,
    )
    artifact = RunArtifact(
        action=Action.FIX,
        tool_results=[result],
        workspace_root=tmp_path,
        total_issues=2,
        total_remaining=2,
        exit_code=1,
    )
    apply_baseline(artifact, baseline)
    result.__post_init__()
    assert_that(result.initial_issues).is_equal_to([new])
    assert_that(result.issues).is_equal_to([new])
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_zero()
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(artifact.total_remaining).is_equal_to(1)
    assert_that(artifact.total_issues).is_equal_to(1)
    assert_that(artifact.exit_code).is_equal_to(1)


def test_acknowledged_fixed_findings_are_not_reported_as_new_fixes(
    tmp_path: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
) -> None:
    """Removing an acknowledged finding from detected also removes its fix credit."""
    baseline = snapshot([dirty_result], root=tmp_path)
    result = replace(
        dirty_result,
        success=True,
        initial_issues=[finding],
        initial_issues_count=1,
        fixed_issues_count=1,
        remaining_issues_count=0,
        issues=[],
        issues_count=0,
    )
    artifact = RunArtifact(
        action=Action.FIX,
        tool_results=[result],
        workspace_root=tmp_path,
        total_fixed=1,
    )
    apply_baseline(artifact, baseline)
    result.__post_init__()
    assert_that(artifact.total_fixed).is_zero()
    assert_that(result.initial_issues).is_empty()
    assert_that(artifact.success).is_true()


@pytest.mark.parametrize("remaining", [0, 1])
def test_unmatched_format_result_preserves_output_and_accounting(
    tmp_path: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
    remaining: int,
) -> None:
    """An unrelated baseline leaves the producer's measured format result intact."""
    baseline = snapshot([dirty_result], root=tmp_path)
    new = replace(finding, code="F999")
    result = replace(
        dirty_result,
        success=remaining == 0,
        issues=[new] if remaining else [],
        issues_count=remaining,
        initial_issues=[new, new],
        initial_issues_count=2,
        fixed_issues_count=2 - remaining,
        remaining_issues_count=remaining,
        output=f"Fixed {2 - remaining} issue(s)",
        formatted_output="producer's formatted output",
    )
    expected = replace(result, baselined_count=0)
    artifact = RunArtifact(
        action=Action.FIX,
        tool_results=[result],
        workspace_root=tmp_path,
        total_issues=remaining,
        total_fixed=2 - remaining,
        total_remaining=remaining,
        exit_code=int(remaining > 0),
    )

    apply_baseline(artifact, baseline)

    assert_that(result).is_equal_to(expected)
    assert_that(artifact.total_fixed).is_equal_to(2 - remaining)
    assert_that(artifact.total_remaining).is_equal_to(remaining)
    assert_that(artifact.exit_code).is_equal_to(int(remaining > 0))


def test_partial_filtering_preserves_successful_producer_verdict(
    tmp_path: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
) -> None:
    """Retained findings still fail the check without flipping the tool's verdict."""
    baseline = snapshot([dirty_result], root=tmp_path)
    new = replace(finding, code="F999")
    result = replace(
        dirty_result,
        success=True,
        issues=[finding, new],
        issues_count=2,
    )
    artifact = RunArtifact(
        tool_results=[result],
        workspace_root=tmp_path,
        total_issues=2,
        total_remaining=2,
        exit_code=1,
    )

    apply_baseline(artifact, baseline)

    assert_that(result.success).is_true()
    assert_that(result.issues).is_equal_to([new])
    assert_that(result.baselined_count).is_equal_to(1)
    assert_that(artifact.total_issues).is_equal_to(1)
    assert_that(artifact.exit_code).is_equal_to(1)


def test_failed_file_cannot_be_hidden_by_peer_findings_in_the_same_tool(
    workspace: Path,
) -> None:
    """A mixed per-file execution failure must stay unknown after aggregation."""
    from unittest.mock import patch

    from lintro.tools.shellcheck.definition import ShellcheckPlugin

    first = workspace / "a.sh"
    second = workspace / "b.sh"
    first.write_text("#!/bin/sh\necho $X\n", encoding="utf-8")
    second.write_text(first.read_text(), encoding="utf-8")
    plugin = ShellcheckPlugin()
    output = (
        '[{"file": "b.sh", "line": 2, "column": 6, "code": 2086, '
        '"level": "warning", "message": "Double quote"}]'
    )
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            plugin,
            "_run_subprocess_result",
            side_effect=[OSError("unreadable"), subprocess_result(False, output)],
        ),
    ):
        result = plugin.check([str(first), str(second)], {})
    assert_that(result.success).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.execution_failed).is_true()
    assert_that(result.output).contains("unreadable")
    assert_that(result.issues_count).is_equal_to(1)
    baseline = snapshot([replace(result, execution_failed=False)], root=workspace)
    artifact = RunArtifact(
        tool_results=[result],
        workspace_root=workspace,
        total_issues=1,
        exit_code=1,
    )
    apply_baseline(artifact, baseline)
    assert_that(artifact.success).is_false()
    assert_that(result.baselined_count).is_zero()
    assert_that(result.output).contains("unreadable")


@pytest.mark.parametrize("inspected", [True, None])
def test_verify_fold_preserves_final_findings_and_inspection_for_baselining(
    tmp_path: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
    inspected: bool | None,
) -> None:
    """Only known final inspection may suppress acknowledged residual findings."""
    from lintro.tools.core.verify_pass import (
        VerifyOutcome,
        VerifyScope,
        fold_verify_results,
    )

    baseline = snapshot([dirty_result], root=tmp_path)
    new = replace(finding, code="F999")
    mutation = replace(
        dirty_result,
        initial_issues=[finding, new],
        initial_issues_count=2,
        fixed_issues_count=2,
        remaining_issues_count=0,
        issues=[],
        issues_count=0,
        success=True,
    )
    verified = replace(
        dirty_result,
        issues=[finding, new],
        issues_count=2,
        inspected_files=inspected,
    )
    results = [mutation]
    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="ruff", result=verified)],
        scope=VerifyScope(files=(str(tmp_path / finding.file),), narrowed=True),
    )
    artifact = RunArtifact(
        action=Action.FIX,
        tool_results=results,
        workspace_root=tmp_path,
        total_issues=2,
        total_remaining=2,
        exit_code=1,
    )
    apply_baseline(artifact, baseline)
    results[0].__post_init__()
    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(artifact.total_fixed).is_zero()
    assert_that(results[0].issues).is_equal_to([new] if inspected else [finding, new])
    assert_that(artifact.total_remaining).is_equal_to(1 if inspected else 2)
    assert_that(results[0].baselined_count).is_equal_to(1 if inspected else 0)
