"""Apply explicit acknowledgements to the final, verified run artifact."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from loguru import logger

from lintro.enums.action import Action
from lintro.models.core.tool_outcome import FixMeasured, FixUnmeasured
from lintro.utils.execution.exit_codes import (
    aggregate_tool_results,
    determine_exit_code,
)
from lintro.utils.fingerprint_baseline import (
    baseline_incomplete_reason,
    filter_issues,
    fingerprint,
)
from lintro.utils.severity_counts import count_severities

if TYPE_CHECKING:
    from pathlib import Path

    from lintro.models.core.fingerprint_baseline import FingerprintBaseline
    from lintro.models.core.run_artifact import RunArtifact
    from lintro.models.core.tool_result import ToolResult
    from lintro.utils.fingerprint_baseline import Fingerprint


def _filter_fix_accounting(result: ToolResult, root: Path) -> None:
    """Match filtered finding occurrences without assuming a residual subset.

    Args:
        result: Complete format result with filtered initial and residual lists.
        root: Invocation workspace root for canonical finding identities.
    """
    initial = len(result.initial_issues)
    populations: list[Counter[Fingerprint]] = []
    accounting: FixMeasured | FixUnmeasured
    for issues in (result.initial_issues, result.issues):
        entries = [
            fingerprint(issue, tool=result.name, root=root, cwd=result.cwd)
            for issue in issues
        ]
        if any(entry is None for entry in entries):
            accounting = FixUnmeasured(
                initial=initial,
                reason="filtered finding identities cannot be matched completely",
            )
            break
        populations.append(Counter(entry.key for entry in entries if entry is not None))
    else:
        before, after = populations
        fixed = sum((before - after).values())
        introduced = sum((after - before).values())
        # FixMeasured requires initial = fixed + remaining. It has no place
        # for introduced findings; retain both populations and report unknown
        # accounting rather than inventing initial findings or netting fixes out.
        accounting = (
            FixUnmeasured(
                initial=initial,
                reason=(
                    f"format introduced {introduced} residual finding(s); "
                    f"{fixed} initial finding(s) resolved, but complete fix "
                    "accounting is unavailable"
                ),
            )
            if introduced
            else FixMeasured(initial, fixed, len(result.issues))
        )
    result.initial_issues_count = accounting.initial
    result.fixed_issues_count = accounting.fixed
    result.remaining_issues_count = accounting.remaining
    result.residual_unknown = isinstance(accounting, FixUnmeasured)
    result.residual_unknown_reason = (
        accounting.reason if isinstance(accounting, FixUnmeasured) else None
    )


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
        if (reason := baseline_incomplete_reason(result)) is not None:
            if not result.skipped and not result.no_files:
                logger.warning(f"Baseline not applied to {result.name}: {reason}")
            continue
        if artifact.action == Action.FIX and (
            not isinstance(result.outcome.accounting, FixMeasured)
            or result.initial_issues_count != len(result.initial_issues)
        ):
            # Preserve incomplete accounting. Empty collection defaults do not
            # prove pre-fix measurement, even beside complete numeric counters.
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
        result.output = None
        if artifact.action == Action.FIX:
            result.initial_issues = detected
            _filter_fix_accounting(result, artifact.workspace_root)
        # Only the producer's completeness proof can explain a failed verdict.
        result.success = (
            result.success or not retained
        ) and not result.residual_unknown
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
