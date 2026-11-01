"""Apply explicit acknowledgements to the final, verified run artifact."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING

from loguru import logger

from lintro.enums.action import Action
from lintro.models.core.tool_outcome import (
    Checked,
    FixMeasured,
    FixUnmeasured,
    ToolOutcome,
)
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
    from collections.abc import Sequence
    from pathlib import Path

    from lintro.models.core.fingerprint_baseline import (
        BaselineFinding,
        FingerprintBaseline,
    )
    from lintro.models.core.run_artifact import RunArtifact
    from lintro.models.core.tool_result import ToolResult
    from lintro.utils.fingerprint_baseline import Fingerprint


def _filter_fix_accounting(
    result: ToolResult,
    root: Path,
    detected: Sequence[BaselineFinding],
    retained: Sequence[BaselineFinding],
) -> FixMeasured | FixUnmeasured:
    """Match filtered finding occurrences without assuming a residual subset.

    Args:
        result: Complete format result with filtered initial and residual lists.
        root: Invocation workspace root for canonical finding identities.
        detected: Filtered pre-fix findings.
        retained: Filtered residual findings.

    Returns:
        Measured accounting, or explicit uncertainty for introduced findings.
    """
    initial = len(detected)
    populations: list[Counter[Fingerprint]] = []
    accounting: FixMeasured | FixUnmeasured
    for issues in (detected, retained):
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
            else FixMeasured(initial, fixed, len(retained))
        )
    return accounting


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
    independent_exit = artifact.exit_code if forced_failure else 0
    for index, result in enumerate(artifact.tool_results):
        result = replace(result, baselined_count=0)
        artifact.tool_results[index] = result
        if (reason := baseline_incomplete_reason(result)) is not None:
            if result.issues or result.initial_issues:
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
        accounting = (
            _filter_fix_accounting(result, artifact.workspace_root, detected, retained)
            if artifact.action == Action.FIX
            else result.outcome.accounting
        )
        # Only the producer's completeness proof can explain a failed verdict.
        outcome = ToolOutcome(
            execution=Checked(
                success=(result.success or not retained)
                and not isinstance(accounting, FixUnmeasured),
                inspected_files=result.inspected_files,
            ),
            accounting=accounting,
            issues_count=len(retained),
        )
        # Replace state and payload together; construction validates every change.
        # Cached text contains acknowledged findings. Execution-error output is
        # protected by the completeness guard above.
        artifact.tool_results[index] = replace(
            result,
            **outcome.to_legacy_fields(),
            issues=retained,
            initial_issues=(
                detected if artifact.action == Action.FIX else result.initial_issues
            ),
            baselined_count=suppressed,
            formatted_output=None,
            output=None,
        )
    totals = aggregate_tool_results(artifact.tool_results, artifact.action)
    artifact.total_issues, artifact.total_fixed, artifact.total_remaining = totals
    artifact.exit_code = determine_exit_code(
        artifact.action,
        artifact.tool_results,
        totals[0],
        totals[2],
    )
    if forced_failure:
        artifact.exit_code = independent_exit or artifact.exit_code or 1
    artifact.severity_counts = count_severities(artifact.tool_results)
    artifact.previous_severity_counts = None
