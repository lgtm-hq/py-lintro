"""Aggregation, scoring, and exit-code resolution for a completed run.

Turns raw tool results into the
:class:`~lintro.models.core.run_artifact.RunArtifact` the render phase
consumes, and re-derives that artifact when a post-execution consumer (today:
the AI layer) mutates the results in place. Split out of the executor as part
of issue #1823.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.enums.action import Action
from lintro.models.core.run_artifact import RunArtifact
from lintro.utils.execution.exit_codes import (
    DEFAULT_EXIT_CODE_FAILURE,
    aggregate_tool_results,
    determine_exit_code,
)

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.utils.execution.run_context import RunContext

__all__ = [
    "finalize_artifact",
    "refresh_artifact",
    "sequential_totals",
]


def finalize_artifact(
    *,
    ctx: RunContext,
    all_results: list[ToolResult],
    total_issues: int,
    total_fixed: int,
    total_remaining: int,
    main_phase_empty_due_to_filter: bool,
    fail_under: float | None,
) -> RunArtifact:
    """Score the run and resolve its exit code into a :class:`RunArtifact`.

    Args:
        ctx: Shared run context.
        all_results: Every tool result collected during the run.
        total_issues: Aggregated issue count.
        total_fixed: Aggregated fixed count.
        total_remaining: Aggregated remaining count.
        main_phase_empty_due_to_filter: Whether post-check filtering emptied
            the main phase.
        fail_under: Optional health-score gate; a score strictly below this
            forces exit code 1.

    Returns:
        RunArtifact: The completed artifact for the render phase.
    """
    from pathlib import Path

    from lintro.utils.health_score import health_score_for_results

    exit_code = int(
        determine_exit_code(
            action=ctx.action,
            all_results=all_results,
            total_issues=total_issues,
            total_remaining=total_remaining,
            main_phase_empty_due_to_filter=main_phase_empty_due_to_filter,
        ),
    )

    # Compute the deterministic 0-100 health score from the aggregated results.
    health = health_score_for_results(
        all_results,
        getattr(ctx.lintro_config, "score", None),
    )

    # CI gate: fail the run when the score falls below the requested threshold.
    if fail_under is not None and health.score < fail_under:
        exit_code = DEFAULT_EXIT_CODE_FAILURE

    return RunArtifact(
        tool_results=all_results,
        action=ctx.action,
        workspace_root=Path.cwd(),
        health=health,
        total_issues=total_issues,
        total_fixed=total_fixed,
        total_remaining=total_remaining,
        exit_code=exit_code,
        dry_run_preview=ctx.dry_run_preview,
        main_phase_empty_due_to_filter=main_phase_empty_due_to_filter,
    )


def refresh_artifact(
    artifact: RunArtifact,
    *,
    ctx: RunContext,
    fail_under: float | None = None,
    force_failure: bool = False,
) -> RunArtifact:
    """Re-aggregate and re-score an artifact whose results were mutated.

    Used after a post-execution consumer (today: the AI layer) has changed the
    tool results in place. The exit code is recomputed from the fresh totals,
    then raised to 1 when the consumer demands failure — before the
    ``fail_under`` gate, so the score gate can still fail a run the consumer
    left at 0.

    Args:
        artifact: The artifact whose results were mutated. Its
            ``main_phase_empty_due_to_filter`` state is carried over so the
            exit code is resolved exactly as it was on the first pass.
        ctx: Shared run context.
        fail_under: Optional health-score gate.
        force_failure: Whether the consumer requires a non-zero exit code.

    Returns:
        RunArtifact: A refreshed artifact carrying the new totals and exit code.
    """
    total_issues, total_fixed, total_remaining = aggregate_tool_results(
        artifact.tool_results,
        ctx.action,
    )
    refreshed = finalize_artifact(
        ctx=ctx,
        all_results=artifact.tool_results,
        total_issues=total_issues,
        total_fixed=total_fixed,
        total_remaining=total_remaining,
        main_phase_empty_due_to_filter=artifact.main_phase_empty_due_to_filter,
        fail_under=fail_under,
    )
    if force_failure:
        refreshed.exit_code = DEFAULT_EXIT_CODE_FAILURE
    return refreshed


def sequential_totals(
    all_results: list[ToolResult],
    action: Action,
) -> tuple[int, int, int]:
    """Accumulate totals the way the sequential execution path always has.

    Deliberately distinct from
    :func:`~lintro.utils.execution.exit_codes.aggregate_tool_results`: that
    helper mirrors ``total_issues`` into ``total_remaining`` outside FIX mode,
    which the sequential path has never done. Preserving the difference keeps
    the execute/render split (issue #1823) behaviour-identical.

    Args:
        all_results: Results collected by the sequential execution path.
        action: The action that was executed.

    Returns:
        tuple[int, int, int]: ``(total_issues, total_fixed, total_remaining)``.
    """
    total_issues = 0
    total_fixed = 0
    total_remaining = 0
    for result in all_results:
        total_issues += getattr(result, "issues_count", 0) or 0
        if action == Action.FIX:
            total_fixed += getattr(result, "fixed_issues_count", None) or 0
            total_remaining += getattr(result, "remaining_issues_count", None) or 0
    return total_issues, total_fixed, total_remaining
