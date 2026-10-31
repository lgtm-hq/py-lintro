"""Apply explicit acknowledgements to the final, verified run artifact."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.enums.action import Action
from lintro.utils.execution.exit_codes import (
    aggregate_tool_results,
    determine_exit_code,
)
from lintro.utils.fingerprint_baseline import filter_issues, result_is_complete
from lintro.utils.severity_counts import count_severities

if TYPE_CHECKING:
    from lintro.models.core.fingerprint_baseline import FingerprintBaseline
    from lintro.models.core.run_artifact import RunArtifact


def apply_baseline(artifact: RunArtifact, baseline: FingerprintBaseline) -> None:
    """Filter final findings and derive all counts from the filtered population.

    Args:
        artifact: Completed run, after AI and the verify pass.
        baseline: Validated explicit acknowledgement file.
    """
    if artifact.early_exit or artifact.action == Action.TEST:
        return
    # Independent gates (including AI policy) must not turn green just because
    # findings were acknowledged. Tool lint failures are recalculated below.
    forced_failure = (
        artifact.force_failure
        or artifact.exit_code
        != determine_exit_code(
            artifact.action,
            artifact.tool_results,
            artifact.total_issues,
            artifact.total_remaining,
        )
    )
    for result in artifact.tool_results:
        result.baselined_count = 0
        if not result_is_complete(result):
            continue
        if artifact.action == Action.FIX and result.initial_issues is None:
            # A residual alone cannot establish which acknowledged findings
            # were fixed. Keep that producer's accounting unchanged.
            continue
        retained, suppressed = filter_issues(
            result.issues or [],
            result=result,
            baseline=baseline,
            root=artifact.workspace_root,
        )
        detected, detected_suppressed = filter_issues(
            result.initial_issues or [] if artifact.action == Action.FIX else [],
            result=result,
            baseline=baseline,
            root=artifact.workspace_root,
        )
        if not suppressed and not detected_suppressed:
            continue
        result.issues = retained
        result.issues_count = len(retained)
        result.baselined_count = suppressed
        # Cached text and native raw output still contain acknowledged findings.
        # Execution-error output is kept by the completeness guard above.
        result.formatted_output = None
        if artifact.action == Action.FIX:
            result.initial_issues = detected
            before = len(detected)
            remaining = len(retained)
            fixed = max(0, before - remaining)
            result.initial_issues_count = fixed + remaining
            result.fixed_issues_count = fixed
            result.remaining_issues_count = remaining
        # Only the producer's completeness proof can explain a failed verdict.
        result.success = result.success or not retained
        if result.success:
            result.output = None
    totals = aggregate_tool_results(artifact.tool_results, artifact.action)
    artifact.total_issues, artifact.total_fixed, artifact.total_remaining = totals
    artifact.exit_code = determine_exit_code(
        artifact.action,
        artifact.tool_results,
        totals[0],
        totals[2],
    )
    if forced_failure:
        artifact.exit_code = 1
    artifact.severity_counts = count_severities(artifact.tool_results)
    artifact.previous_severity_counts = None
