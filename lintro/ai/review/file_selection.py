"""Reconciliation of which changed files a review round actually looked at.

Two independent stages can drop a changed file: context collection (a
``--path`` filter) and chunking (repetitive-diff sampling). Each records its
own :class:`~lintro.ai.review.models.skipped_file.SkippedFile` entries; this
module merges them into the single reviewed/skipped split that run metadata
and the per-review comment body report (#1910).

An agents-only run (``review.custom_agents: only``) adds a third stage:
:func:`agent_scope_skips` records the changed files no custom agent covered,
which without the record would report as reviewed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.ai.review.enums.file_skip_reason import FileSkipReason
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.skipped_file import SkippedFile

if TYPE_CHECKING:
    from lintro.ai.review.custom_agents import CustomAgentSelection

__all__ = ["FileSelection", "agent_scope_skips", "resolve_file_selection"]


@dataclass(frozen=True, slots=True)
class FileSelection:
    """The reviewed/skipped split for one review round.

    Attributes:
        reviewed_paths: Repository-relative paths the review looked at, sorted.
        skipped: Excluded files with their reasons, sorted by path.
    """

    reviewed_paths: tuple[str, ...]
    skipped: tuple[SkippedFile, ...]


def resolve_file_selection(
    *,
    context: ReviewContext,
    chunk_skips: Sequence[SkippedFile] = (),
) -> FileSelection:
    """Split a round's changed files into reviewed and skipped sets.

    A path skipped by more than one stage keeps the earliest reason recorded
    for it, so a file dropped by the ``--path`` filter is never re-explained
    as a sampling omission.

    Args:
        context: Collected review context, carrying any collection-stage skips.
        chunk_skips: Per-file skips recorded by the chunker.

    Returns:
        The reviewed paths and the skipped files with their reasons.
    """
    skipped_by_path: dict[str, SkippedFile] = {}
    for entry in (*context.skipped_files, *chunk_skips):
        skipped_by_path.setdefault(entry.path, entry)

    reviewed = sorted(
        {
            changed_file.path
            for changed_file in context.changed_files
            if changed_file.path not in skipped_by_path
        },
    )
    skipped = tuple(skipped_by_path[path] for path in sorted(skipped_by_path))
    return FileSelection(reviewed_paths=tuple(reviewed), skipped=skipped)


def agent_scope_skips(
    *,
    context: ReviewContext,
    selection: CustomAgentSelection,
    run_builtin_checklist: bool,
    completed_agents: frozenset[str],
) -> list[SkippedFile]:
    """List changed files no custom agent reviewed in an agents-only run.

    Under ``review.custom_agents: only`` the built-in checklist never runs, so
    a file no agent looked at is not reviewed at all. Without this record the
    run would report it as reviewed and the gap would read as a clean pass.

    Coverage is credited from the agents that *completed*, never from the ones
    that were merely selected. A selected agent can fail to produce a pass in
    two ways — a non-budget ``AIError`` skips it and the run continues, or a
    cost-cap stop means later agents never start — and in both cases its files
    were scheduled but never read.

    Args:
        context: Collected review context.
        selection: Custom agents partitioned into selected and skipped.
        run_builtin_checklist: Whether the built-in checklist passes ran.
        completed_agents: Names of the agents that returned a completed pass.

    Returns:
        Skip records for the uncovered files; empty when the checklist ran
        (it covers every changed file).
    """
    if run_builtin_checklist:
        return []
    covered = {
        path
        for agent in selection.selected
        if agent.agent.name in completed_agents
        for path in agent.files
    }
    return [
        SkippedFile(path=changed.path, reason=FileSkipReason.AGENT_SCOPE)
        for changed in context.changed_files
        if changed.path not in covered
    ]
